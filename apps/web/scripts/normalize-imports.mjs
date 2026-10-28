/**
 * Preview, explicitly apply, or check local import policy using TypeScript resolution.
 * Only src/ and tests/ are scanned. Valid aliases and nearby relative imports stay
 * unchanged. Strict unresolved-import checks finish before writes begin; filesystem
 * write failures can still leave partial edits, so review the Git diff after fixes.
 * @example
 * // npm run imports:preview
 * // npm run imports:fix
 * // npm run imports:check
 */
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const frontendRoot = path.resolve(scriptDirectory, '..');
const srcRoot = path.join(frontendRoot, 'src');
const testsRoot = path.join(frontendRoot, 'tests');
const tsconfigPath = path.join(frontendRoot, 'tsconfig.json');

const flags = new Set(process.argv.slice(2));
const shouldWrite = flags.has('--write');
const shouldCheck = flags.has('--check');
const strict = flags.has('--strict');
const verbose = flags.has('--verbose') || !shouldWrite;

if (flags.has('--help')) {
  console.log(`Usage:
  node scripts/normalize-imports.mjs             Preview changes
  node scripts/normalize-imports.mjs --write     Apply changes
  node scripts/normalize-imports.mjs --check     Exit 1 when changes are needed
  node scripts/normalize-imports.mjs --strict    Fail on unresolved local imports
  node scripts/normalize-imports.mjs --verbose   Print every replacement
`);
  process.exit(0);
}

if (shouldWrite && shouldCheck) {
  console.error('Choose either --write or --check, not both.');
  process.exit(2);
}

const allowedFlags = new Set([
  '--write',
  '--check',
  '--strict',
  '--verbose',
  '--help',
]);
const unknownFlags = [...flags].filter((flag) => !allowedFlags.has(flag));

if (unknownFlags.length > 0) {
  console.error(`Unknown flag(s): ${unknownFlags.join(', ')}`);
  process.exit(2);
}

/**
 * Format compiler diagnostics using the frontend directory as their context.
 * @param {readonly ts.Diagnostic[]} diagnostics Configuration diagnostics from TypeScript.
 * @returns {string} Formatted diagnostic text.
 */
function formatDiagnostics(diagnostics) {
  return ts.formatDiagnosticsWithColorAndContext(diagnostics, {
    getCanonicalFileName: (fileName) => fileName,
    getCurrentDirectory: () => frontendRoot,
    getNewLine: () => '\n',
  });
}

const configFile = ts.readConfigFile(tsconfigPath, ts.sys.readFile);

if (configFile.error !== undefined) {
  console.error(formatDiagnostics([configFile.error]));
  process.exit(2);
}

const parsedConfig = ts.parseJsonConfigFileContent(
  configFile.config,
  ts.sys,
  frontendRoot,
  undefined,
  tsconfigPath,
);

if (parsedConfig.errors.length > 0) {
  console.error(formatDiagnostics(parsedConfig.errors));
  process.exit(2);
}

const compilerOptions = parsedConfig.options;
const ignoredDirectoryNames = new Set([
  '.git',
  '.vite-cache',
  'coverage',
  'dist',
  'node_modules',
]);

/**
 * Select application TypeScript files while leaving declaration files untouched.
 * @param {string} filePath Candidate source path.
 * @returns {boolean} Whether the file is supported by this tool.
 */
function isTypeScriptSource(filePath) {
  return (
    /\.(?:ts|tsx|mts|cts)$/i.test(filePath) &&
    !/\.d\.(?:ts|mts|cts)$/i.test(filePath)
  );
}

/**
 * Collect source files in deterministic order without traversing symbolic links.
 * @param {string} rootDirectory Source or test directory; a missing directory is empty.
 * @returns {string[]} Sorted supported file paths.
 * @throws {Error} A source directory cannot be read.
 */
function collectFiles(rootDirectory) {
  if (!fs.existsSync(rootDirectory)) {
    return [];
  }

  const files = [];
  const pendingDirectories = [rootDirectory];

  while (pendingDirectories.length > 0) {
    const currentDirectory = pendingDirectories.pop();

    if (currentDirectory === undefined) {
      continue;
    }

    for (const entry of fs.readdirSync(currentDirectory, {
      withFileTypes: true,
    })) {
      const entryPath = path.join(currentDirectory, entry.name);

      if (entry.isDirectory()) {
        if (!ignoredDirectoryNames.has(entry.name)) {
          pendingDirectories.push(entryPath);
        }
        continue;
      }

      if (entry.isFile() && isTypeScriptSource(entryPath)) {
        files.push(entryPath);
      }
    }
  }

  return files.sort();
}

/**
 * Check lexical containment without accepting parent or absolute path escapes.
 * @param {string} parentDirectory Allowed directory.
 * @param {string} candidatePath Candidate path.
 * @returns {boolean} Whether the candidate is lexically contained.
 */
function isInside(parentDirectory, candidatePath) {
  const relativePath = path.relative(parentDirectory, candidatePath);

  return (
    relativePath === '' ||
    (!relativePath.startsWith(`..${path.sep}`) &&
      relativePath !== '..' &&
      !path.isAbsolute(relativePath))
  );
}

/**
 * Normalize platform separators for an import specifier or displayed path.
 * @param {string} filePath Native path.
 * @returns {string} Path with forward slashes.
 */
function toPosix(filePath) {
  return filePath.split(path.sep).join('/');
}

/**
 * Separate Vite query/hash suffixes from the path used for resolution.
 * @param {string} specifier Cooked module literal.
 * @returns {{base: string, suffix: string}} Resolution path and unchanged Vite suffix.
 */
function splitSpecifierSuffix(specifier) {
  const suffixIndex = specifier.search(/[?#]/u);

  if (suffixIndex === -1) {
    return { base: specifier, suffix: '' };
  }

  return {
    base: specifier.slice(0, suffixIndex),
    suffix: specifier.slice(suffixIndex),
  };
}

/**
 * Resolve a literal Vite asset after TypeScript's module resolver declines it.
 * @param {string} basePath Exact asset path; extension guessing is deliberately avoided.
 * @returns {string | null} Existing file or null.
 */
function resolveCandidate(basePath) {
  return fs.existsSync(basePath) && fs.statSync(basePath).isFile()
    ? path.resolve(basePath)
    : null;
}

/**
 * Resolve code with the existing TypeScript configuration, then literal Vite assets.
 * @param {string} specifier Local specifier without a Vite suffix.
 * @param {string} containingFile Absolute importer path.
 * @returns {string | null} Resolved absolute path, or null when unresolved.
 */
function resolveModulePath(specifier, containingFile) {
  const resolvedByTypeScript = ts.resolveModuleName(
    specifier,
    containingFile,
    compilerOptions,
    ts.sys,
  ).resolvedModule?.resolvedFileName;

  if (resolvedByTypeScript !== undefined) {
    return path.resolve(resolvedByTypeScript);
  }

  if (specifier.startsWith('@/')) {
    return resolveCandidate(path.join(srcRoot, specifier.slice(2)));
  }

  if (specifier.startsWith('.')) {
    return resolveCandidate(
      path.resolve(path.dirname(containingFile), specifier),
    );
  }

  return null;
}

/**
 * Build an extensionless code stem, preserving non-code asset extensions.
 * @param {string} resolvedFilePath Resolved module path.
 * @returns {string} Candidate stem; callers must verify that index collapsing preserves resolution.
 */
function moduleStem(resolvedFilePath) {
  let stem = resolvedFilePath
    .replace(/\.d\.(?:ts|mts|cts)$/iu, '')
    .replace(/\.[cm]?[jt]sx?$/iu, '');

  if (path.basename(stem).toLowerCase() === 'index') {
    stem = path.dirname(stem);
  }

  return stem;
}

/**
 * Identify a feature boundary from the repository source layout.
 * @param {string} filePath Absolute source path.
 * @returns {string | null} Feature identity, or null outside a recognized feature.
 */
function architectureOwner(filePath) {
  if (!isInside(srcRoot, filePath)) {
    return null;
  }

  const parts = toPosix(path.relative(srcRoot, filePath)).split('/');

  if (parts[0] === 'features' && parts[1] !== undefined) {
    return `feature:${parts[1]}`;
  }

  return null;
}

/**
 * Construct a candidate using the existing source alias.
 * @param {string} resolvedFilePath Resolved target within src.
 * @returns {string} Alias candidate, subject to resolution verification.
 */
function buildAliasSpecifier(resolvedFilePath) {
  const relativePath = toPosix(
    path.relative(srcRoot, moduleStem(resolvedFilePath)),
  );

  return `@/${relativePath}`;
}

/**
 * Construct a nearby relative candidate using native path relationships.
 * @param {string} containingFile Absolute importer path.
 * @param {string} resolvedFilePath Absolute target path.
 * @returns {string} Relative candidate with forward slashes.
 */
function buildRelativeSpecifier(containingFile, resolvedFilePath) {
  let relativePath = toPosix(
    path.relative(path.dirname(containingFile), moduleStem(resolvedFilePath)),
  );

  if (!relativePath.startsWith('.')) {
    relativePath = `./${relativePath}`;
  }

  return relativePath;
}

/**
 * Count leading parent traversals without treating embedded segments as parents.
 * @param {string} specifier Relative import path.
 * @returns {number} Number of consecutive leading parent segments.
 */
function leadingParentCount(specifier) {
  let count = 0;

  for (const segment of specifier.split('/')) {
    if (segment !== '..') {
      break;
    }
    count += 1;
  }

  return count;
}

/**
 * Choose an alias across feature boundaries or for deep relative dependencies.
 * @param {string} containingFile Absolute importer path.
 * @param {string} resolvedFilePath Absolute resolved target.
 * @returns {string | null} Candidate spelling, or null for targets outside src.
 */
function preferredSpecifier(containingFile, resolvedFilePath) {
  if (!isInside(srcRoot, resolvedFilePath)) {
    return null;
  }

  const aliasSpecifier = buildAliasSpecifier(resolvedFilePath);

  // Tests should use the application alias for production source.
  if (isInside(testsRoot, containingFile)) {
    return aliasSpecifier;
  }

  if (!isInside(srcRoot, containingFile)) {
    return aliasSpecifier;
  }

  const relativeSpecifier = buildRelativeSpecifier(
    containingFile,
    resolvedFilePath,
  );
  const sourceOwner = architectureOwner(containingFile);
  const targetOwner = architectureOwner(resolvedFilePath);
  const crossesArchitectureBoundary =
    sourceOwner !== null && targetOwner !== null && sourceOwner !== targetOwner;
  const isDeepRelativeImport = leadingParentCount(relativeSpecifier) >= 2;

  // Policy:
  // - same folder or one parent inside the same domain: relative import
  // - two or more parent traversals: @/ alias
  // - cross-feature dependency: @/ alias
  return crossesArchitectureBoundary || isDeepRelativeImport
    ? aliasSpecifier
    : relativeSpecifier;
}

/**
 * Narrow AST nodes to static module literals without template expressions.
 * @param {ts.Node} node Candidate AST node.
 * @returns {node is ts.StringLiteral | ts.NoSubstitutionTemplateLiteral} Whether the node contains a statically known string.
 */
function isStringModuleSpecifier(node) {
  return ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node);
}

/**
 * Find import/export literals, single-argument static dynamic imports, and import types.
 * @param {ts.SourceFile} sourceFile Parsed TypeScript source; comments and ordinary strings are ignored.
 * @returns {(ts.StringLiteral | ts.NoSubstitutionTemplateLiteral)[]} Literal nodes in traversal order.
 */
function collectModuleSpecifierNodes(sourceFile) {
  /** @type {(ts.StringLiteral | ts.NoSubstitutionTemplateLiteral)[]} */
  const nodes = [];

  /** @param {ts.Node} node */
  function visit(node) {
    if (
      ts.isImportDeclaration(node) &&
      isStringModuleSpecifier(node.moduleSpecifier)
    ) {
      nodes.push(node.moduleSpecifier);
    } else if (
      ts.isExportDeclaration(node) &&
      node.moduleSpecifier !== undefined &&
      isStringModuleSpecifier(node.moduleSpecifier)
    ) {
      nodes.push(node.moduleSpecifier);
    } else if (
      ts.isImportEqualsDeclaration(node) &&
      ts.isExternalModuleReference(node.moduleReference) &&
      node.moduleReference.expression !== undefined &&
      isStringModuleSpecifier(node.moduleReference.expression)
    ) {
      nodes.push(node.moduleReference.expression);
    } else if (ts.isCallExpression(node) && node.arguments.length === 1) {
      const argument = node.arguments[0];
      const isDynamicImport =
        node.expression.kind === ts.SyntaxKind.ImportKeyword;

      if (
        argument !== undefined &&
        isDynamicImport &&
        isStringModuleSpecifier(argument)
      ) {
        nodes.push(argument);
      }
    }

    if (
      ts.isImportTypeNode(node) &&
      ts.isLiteralTypeNode(node.argument) &&
      ts.isStringLiteral(node.argument.literal)
    ) {
      nodes.push(node.argument.literal);
    }
    ts.forEachChild(node, visit);
  }

  visit(sourceFile);
  return nodes;
}

/**
 * Choose the parser mode from the source extension.
 * @param {string} filePath Source file path.
 * @returns {ts.ScriptKind} TypeScript or TSX parser mode.
 */
function scriptKindFor(filePath) {
  if (/\.tsx$/iu.test(filePath)) {
    return ts.ScriptKind.TSX;
  }
  if (/\.jsx$/iu.test(filePath)) {
    return ts.ScriptKind.JSX;
  }
  return ts.ScriptKind.TS;
}

/**
 * Escape a rewritten literal without introducing quotes or template interpolation.
 * @param {string} value Resolved import spelling with its Vite suffix.
 * @param {string} quote Original string delimiter.
 * @returns {string} Literal content safe for the original delimiter.
 */
function escapeSpecifier(value, quote) {
  return value
    .replaceAll('\\', '\\\\')
    .replaceAll(quote, (character) => '\\' + character)
    .replaceAll('${', '\\${');
}

const files = [...collectFiles(srcRoot), ...collectFiles(testsRoot)];
/** @type {{filePath: string, text: string}[]} */
const pendingWrites = [];

let changedFileCount = 0;
let replacementCount = 0;
let unresolvedCount = 0;

for (const filePath of files) {
  const originalText = fs.readFileSync(filePath, 'utf8');
  const sourceFile = ts.createSourceFile(
    filePath,
    originalText,
    ts.ScriptTarget.Latest,
    true,
    scriptKindFor(filePath),
  );
  const replacements = [];

  for (const moduleNode of collectModuleSpecifierNodes(sourceFile)) {
    const originalSpecifier = moduleNode.text;

    if (
      !originalSpecifier.startsWith('.') &&
      !originalSpecifier.startsWith('@/')
    ) {
      continue;
    }

    const { base, suffix } = splitSpecifierSuffix(originalSpecifier);
    const resolvedFilePath = resolveModulePath(base, filePath);

    if (resolvedFilePath === null) {
      unresolvedCount += 1;
      console.warn(
        `[unresolved] ${toPosix(path.relative(frontendRoot, filePath))}: ${originalSpecifier}`,
      );
      continue;
    }

    if (originalSpecifier.startsWith('@/')) continue;
    if (!isInside(srcRoot, resolvedFilePath)) continue;
    if (!isInside(fs.realpathSync(srcRoot), fs.realpathSync(resolvedFilePath)))
      continue;
    const sourceOwner = architectureOwner(filePath);
    const targetOwner = architectureOwner(resolvedFilePath);
    const crossFeature =
      sourceOwner !== null &&
      targetOwner !== null &&
      sourceOwner !== targetOwner;
    if (leadingParentCount(base) < 2 && !crossFeature) continue;
    const preferredBase = preferredSpecifier(filePath, resolvedFilePath);

    if (preferredBase === null) {
      continue;
    }

    // Collapsing an index file must never select a neighboring module instead.
    if (resolveModulePath(preferredBase, filePath) !== resolvedFilePath)
      continue;
    const nextSpecifier = preferredBase + suffix;

    if (nextSpecifier === originalSpecifier) {
      continue;
    }

    const { line, character } = sourceFile.getLineAndCharacterOfPosition(
      moduleNode.getStart(sourceFile),
    );

    replacements.push({
      start: moduleNode.getStart(sourceFile) + 1,
      end: moduleNode.getEnd() - 1,
      text: escapeSpecifier(
        nextSpecifier,
        originalText[moduleNode.getStart(sourceFile)] ?? '"',
      ),
      line: line + 1,
      column: character + 1,
      from: originalSpecifier,
      to: nextSpecifier,
    });
  }

  if (replacements.length === 0) {
    continue;
  }

  changedFileCount += 1;
  replacementCount += replacements.length;

  if (verbose) {
    console.log(`\n${toPosix(path.relative(frontendRoot, filePath))}`);
    for (const replacement of replacements) {
      console.log(
        `  ${replacement.line}:${replacement.column}  ${replacement.from} -> ${replacement.to}`,
      );
    }
  }

  let updatedText = originalText;

  for (const replacement of replacements.sort(
    (left, right) => right.start - left.start,
  )) {
    updatedText =
      updatedText.slice(0, replacement.start) +
      replacement.text +
      updatedText.slice(replacement.end);
  }

  if (shouldWrite) {
    pendingWrites.push({ filePath, text: updatedText });
  }
}

if (strict && unresolvedCount > 0) {
  console.error(
    'Import normalization stopped: unresolved local imports. No files were written.',
  );
  process.exit(2);
}

let mode = 'preview';

if (shouldWrite) {
  mode = 'updated';
} else if (shouldCheck) {
  mode = 'check';
}

console.log(`\nImport normalization ${mode} complete.`);
console.log(`Files scanned   : ${files.length}`);
console.log(`Files affected  : ${changedFileCount}`);
console.log(`Imports changed : ${replacementCount}`);
console.log(`Unresolved      : ${unresolvedCount}`);

for (const update of pendingWrites)
  fs.writeFileSync(update.filePath, update.text, 'utf8');

if (shouldCheck && replacementCount > 0) {
  process.exit(1);
}
