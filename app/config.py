"""Environment-driven settings (pydantic-settings)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.contracts import ResourceLimits


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- API ---
    api_bearer_token: str = "dev-token-change-me"
    max_task_bytes: int = 8_192
    max_code_bytes: int = 262_144

    # --- Temporal ---
    temporal_host: str = "temporal:7233"
    temporal_namespace: str = "default"
    task_queue: str = "agent-pipeline"
    workflow_run_timeout_s: int = 300

    # --- Temporal activity policy ---
    agent_start_to_close_s: int = 10
    sandbox_start_to_close_s: int = 60
    sandbox_heartbeat_s: int = 10
    # Max queue wait before a worker picks up an activity; sized to tolerate cold start.
    schedule_to_start_s: int = 30
    max_attempts: int = 3

    # --- Sandbox ---
    sandbox_runtime: str = "runsc"  # runsc (gVisor) | runc (weaker fallback)
    sandbox_image: str = "sandbox-runner:pinned"
    sandbox_cpus: float = 1.0
    sandbox_memory_mb: int = 256
    sandbox_pids: int = 128
    sandbox_wall_clock_s: int = 30
    sandbox_output_max_bytes: int = 1_048_576
    sandbox_tmpfs_mb: int = 64
    sandbox_nofile: int = 256
    sandbox_seccomp_path: str = ""
    # Staging dir for code/input. Must be a bind mount with the same path on host and worker
    # so the sibling sandbox container can mount the files. Blank = system temp.
    sandbox_share_dir: str = ""

    # --- Observability ---
    otel_exporter_otlp_endpoint: str = "http://otel-collector:4317"
    otel_service_name: str = "agent-sandbox"
    trace_payloads: bool = False
    log_level: str = "INFO"

    def resource_limits(self) -> ResourceLimits:
        """Build ResourceLimits from the configured ceilings."""
        return ResourceLimits(
            cpus=self.sandbox_cpus,
            memory_mb=self.sandbox_memory_mb,
            pids=self.sandbox_pids,
            wall_clock_s=self.sandbox_wall_clock_s,
            output_max_bytes=self.sandbox_output_max_bytes,
            tmpfs_mb=self.sandbox_tmpfs_mb,
            nofile=self.sandbox_nofile,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
