"""Export a finished experiment to SigNoz as traces.

Two rules shape this module.

*Telemetry runs after the decision, never before it.* The verdict is computed from the
local evidence and is already final by the time anything is sent. A collector that is
down, slow, or double-counting cannot change what MORROW decided — which is the only way
"the decision is reproducible from the evidence" can be true.

*Only closed values are exported.* Span attributes carry the same opaque references and
enum members the cassettes do. SigNoz is a published surface, so a real path or a shell
command body must not reach it any more than it reaches a committed artifact.

The trace shape is what makes the product legible at a glance: one span per experiment,
one per pair, one per run, and one per agent action inside the run. Put the baseline and
candidate runs side by side and the difference in work is the picture.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

from morrow.domain.events import AgentEvent, CompletionEvent, EventKind

DEFAULT_ENDPOINT = "localhost:4317"
SERVICE_NAME = "morrow"

#: Span names, kept stable so a dashboard query does not drift with refactoring.
EXPERIMENT_SPAN = "morrow.experiment"
PAIR_SPAN = "morrow.pair"
RUN_SPAN = "morrow.run"


@dataclass(frozen=True)
class RunTelemetry:
    """One agent run's worth of exportable facts."""

    run_id: str
    variant: str
    pair_id: int
    attempt_index: int
    adopted: bool
    terminal_status: str
    wall_duration_ms: int
    events: Sequence[AgentEvent]
    files_read_distinct: int
    test_cycles: int
    final_churn: int


@dataclass(frozen=True)
class ExperimentTelemetry:
    experiment_id: str
    scenario_id: str
    evidence_mode: str
    provider: str
    model: str
    policy_sha256: str
    verdict: str
    primary_reason: str
    ffr_gate: float | None
    runs: Sequence[RunTelemetry]


def build_provider(endpoint: str = DEFAULT_ENDPOINT) -> TracerProvider:
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    return provider


def _set_common(span: Span, experiment: ExperimentTelemetry) -> None:
    span.set_attribute("morrow.experiment.id", experiment.experiment_id)
    span.set_attribute("morrow.scenario.id", experiment.scenario_id)
    span.set_attribute("morrow.evidence_mode", experiment.evidence_mode)
    span.set_attribute("morrow.agent.provider", experiment.provider)
    span.set_attribute("morrow.agent.model", experiment.model)
    span.set_attribute("morrow.policy.sha256", experiment.policy_sha256)


def _event_span_name(event: AgentEvent) -> str:
    return {
        EventKind.SESSION_START: "agent.session",
        EventKind.PLAN: "agent.plan",
        EventKind.SEARCH: "repository.search",
        EventKind.FILE_READ: "file.read",
        EventKind.PATCH: "patch.apply",
        EventKind.COMMAND: "command.run",
        EventKind.TEST: "test.run",
        EventKind.TOOL_OTHER: "agent.tool",
        EventKind.COMPLETION: "agent.complete",
        EventKind.OPAQUE: "agent.opaque",
    }[event.kind]


def _export_event(tracer: trace.Tracer, event: AgentEvent) -> None:
    """One span per agent action. This is the trajectory the product is about."""
    with tracer.start_as_current_span(_event_span_name(event)) as span:
        span.set_attribute("morrow.event.seq", event.seq)
        span.set_attribute("morrow.event.kind", event.kind.value)
        span.set_attribute("morrow.event.raw_kind", event.raw_kind.value)
        if event.tool_ref is not None:
            span.set_attribute("morrow.event.tool_ref", event.tool_ref)
        path_ref = getattr(event, "path_ref", None)
        if path_ref is not None:
            span.set_attribute("morrow.event.path_ref", path_ref)
        if event.success is False:
            # A failed tool call is the visible shape of trial and error, so it is marked
            # rather than left for the reader to infer from an attribute.
            span.set_status(Status(StatusCode.ERROR, "tool call failed"))
        if isinstance(event, CompletionEvent):
            span.set_attribute("morrow.agent.num_turns", event.num_turns)
            span.set_attribute("morrow.agent.output_tokens", event.output_tokens)
            span.set_attribute("morrow.agent.cost_micro_usd", event.cost_micro_usd)
            span.set_attribute("morrow.agent.stop_reason", event.stop_reason.value)
            span.set_attribute("morrow.agent.terminal_reason", event.terminal_reason.value)


def _export_run(tracer: trace.Tracer, experiment: ExperimentTelemetry, run: RunTelemetry) -> None:
    with tracer.start_as_current_span(RUN_SPAN) as span:
        _set_common(span, experiment)
        span.set_attribute("morrow.run.id", run.run_id)
        span.set_attribute("morrow.variant", run.variant)
        span.set_attribute("morrow.pair.id", run.pair_id)
        span.set_attribute("morrow.run.attempt_index", run.attempt_index)
        span.set_attribute("morrow.run.adopted", run.adopted)
        span.set_attribute("morrow.run.terminal_status", run.terminal_status)
        span.set_attribute("morrow.run.wall_duration_ms", run.wall_duration_ms)
        span.set_attribute("morrow.metric.files_read_distinct", run.files_read_distinct)
        span.set_attribute("morrow.metric.test_cycles", run.test_cycles)
        span.set_attribute("morrow.metric.final_churn", run.final_churn)
        span.set_attribute("morrow.event.total", len(run.events))

        # Opaque events are kept in the cassette but not turned into spans. A single toy
        # run produced 108 of them against 22 real actions, and a trace where the work is
        # buried under thinking-token markers cannot answer "where did the extra effort
        # go" — which is the only question this view exists to answer.
        exported = [e for e in run.events if e.kind is not EventKind.OPAQUE]
        span.set_attribute("morrow.event.exported", len(exported))
        for event in exported:
            _export_event(tracer, event)


def export_experiment(
    experiment: ExperimentTelemetry,
    *,
    endpoint: str = DEFAULT_ENDPOINT,
    provider: TracerProvider | None = None,
) -> str:
    """Send one experiment and return its trace id.

    The trace id goes into the report so a reader can jump from the verdict straight to
    the evidence in SigNoz.
    """
    owned = provider is None
    tracer_provider = provider if provider is not None else build_provider(endpoint)
    tracer = tracer_provider.get_tracer("morrow.export")

    pairs: dict[int, list[RunTelemetry]] = {}
    for run in experiment.runs:
        pairs.setdefault(run.pair_id, []).append(run)

    with tracer.start_as_current_span(EXPERIMENT_SPAN) as root:
        _set_common(root, experiment)
        root.set_attribute("morrow.verdict", experiment.verdict)
        root.set_attribute("morrow.primary_reason", experiment.primary_reason)
        if experiment.ffr_gate is not None:
            root.set_attribute("morrow.future_friction_ratio", experiment.ffr_gate)
        trace_id = format(root.get_span_context().trace_id, "032x")

        for pair_id in sorted(pairs):
            with tracer.start_as_current_span(PAIR_SPAN) as pair_span:
                _set_common(pair_span, experiment)
                pair_span.set_attribute("morrow.pair.id", pair_id)
                # Baseline first inside the span tree regardless of execution order, so
                # the two sides line up visually when the trace is opened.
                for run in sorted(pairs[pair_id], key=lambda r: r.variant):
                    _export_run(tracer, experiment, run)

    tracer_provider.force_flush()
    if owned:
        tracer_provider.shutdown()
    return trace_id
