import { useState, useEffect } from "react";
import { getApiUrl } from "../utils/api";

export default function PipelineStatus() {
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetch(getApiUrl("/health/pipeline"))
      .then(res => {
        if (!res.ok) throw new Error("Network response was not ok");
        return res.json();
      })
      .then(data => {
        setStatus(data);
        setError(false);
      })
      .catch(err => {
        console.error("Pipeline health check failed:", err);
        setError(true);
      });
  }, []);

  if (error) {
    return (
      <div className="pipeline-status error">
        ⚠ DATA UNAVAILABLE — Pipeline health endpoint unreachable
      </div>
    );
  }

  if (!status) return null;

  const { data, signals, pipeline } = status;
  
  if (status.status === "healthy") {
    return (
      <div className="pipeline-status healthy">
        ✓ Data: {data.stocks_with_today_data}/{data.stocks_active} stocks · 
        Pipeline: {pipeline.last_run_status} · 
        Signals: {signals.count > 0 ? "generated" : "none"}
      </div>
    );
  }

  return (
    <div className={`pipeline-status ${status.status}`}>
      ⚠ DATA {status.status.toUpperCase()} — {data.stocks_with_today_data}/{data.stocks_active} stocks
      {pipeline.last_run_status === "failed" ? " (Pipeline Failed)" : ""}
      {signals.count === 0 ? " · Signals blocked" : ""}
    </div>
  );
}
