import { Component, type ReactNode } from 'react';

export default class ChartBoundary extends Component<
  { readonly children: ReactNode },
  { failed: boolean }
> {
  override state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  override render() {
    return this.state.failed ? (
      <p role="alert" className="notice">
        Charts could not be displayed. Use the measured results table below;
        reload to retry charts.
      </p>
    ) : (
      this.props.children
    );
  }
}
