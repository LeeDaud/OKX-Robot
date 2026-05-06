import { Component } from "react";

interface Props {
  children: React.ReactNode;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{
          padding: "40px",
          maxWidth: "720px",
          margin: "0 auto",
          fontFamily: "monospace",
          color: "#be4b4e",
        }}>
          <h2 style={{ fontSize: "20px", marginBottom: "16px" }}>应用出错</h2>
          <pre style={{
            background: "#ffe0e3",
            padding: "16px",
            borderRadius: "12px",
            overflow: "auto",
            fontSize: "13px",
            lineHeight: "1.5",
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
          }}>
            {this.state.error.message}
            {"\n\n"}
            {this.state.error.stack}
          </pre>
          <button
            onClick={() => { this.setState({ error: null }); window.location.reload(); }}
            style={{
              marginTop: "16px",
              padding: "8px 24px",
              borderRadius: "8px",
              border: "none",
              background: "#248e93",
              color: "white",
              cursor: "pointer",
              fontSize: "14px",
            }}
          >
            刷新页面
          </button>
        </div>
      );
    }

    return this.props.children;
  }
}
