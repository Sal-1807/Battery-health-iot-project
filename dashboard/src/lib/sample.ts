/**
 * Sample Demonstration Result: fixed illustrative values for presentations.
 * They are NOT measured, NOT computed from any battery's data and NOT a model output.
 * They exist only in the dashboard: never sent to the backend, stored in SQLite or mixed
 * with telemetry, Quick Test results or ML predictions.
 */
/** Sample Demonstration mode: the SoH/RUL cards and summary table show SAMPLE_RESULT instead of the computed estimate. */
export const SAMPLE_MODE = true;

export const SAMPLE_RESULT = {
  label: "Sample Demonstration Result",
  soh_pct: 91.6,
  rul_cycles: 84,
} as const;
