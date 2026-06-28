#!/usr/bin/env python3
"""
GA Pipeline: Watchdog + Event Pipeline for GenericAgent
Inspired by cyberboss app.js:
  - Watchdog: 8s notification + 45s failure timeout (threading.Timer)
  - EventPipeline: sequential processing with exception isolation (queue.Queue)
"""

import threading
import queue
import time
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("ga_pipeline")


# ============================================================
# Event Pipeline (cyberboss runtimeEventChain pattern)
# ============================================================

@dataclass
class GAEvent:
    """Event that flows through the pipeline."""
    type: str           # tool_start, tool_end, tool_error, turn_start, turn_end,
                        # watchdog_warning, watchdog_timeout, llm_call, llm_response
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    turn: int = 0

    def __str__(self):
        return f"GAEvent({self.type}, turn={self.turn}, payload_keys={list(self.payload.keys())})"


class EventPipeline:
    """
    Ordered event processing pipeline with exception isolation.
    
    Pattern from cyberboss:
      this.runtimeEventChain = this.runtimeEventChain
        .catch(() => {})  // ← exception isolation
        .then(() => this.handleRuntimeEvent(event))
        .catch((error) => { console.error(...); });
    
    Python equivalent: queue.Queue + per-handler try/except.
    """
    def __init__(self, maxsize: int = 1000):
        self._queue: queue.Queue = queue.Queue(maxsize=maxsize)
        self._handlers: Dict[str, List[Callable[[GAEvent], None]]] = {}
        self._history: List[GAEvent] = []  # limited ring buffer for diagnostics
        self._max_history: int = 200
        self._emit_count: int = 0

    # --- Handler registration ---
    def on(self, event_type: str, handler: Callable[[GAEvent], None]) -> None:
        """Register a handler for a specific event type. Multiple handlers per type allowed."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)

    def on_any(self, handler: Callable[[GAEvent], None]) -> None:
        """Register a handler for ALL event types (global observer)."""
        self.on("*", handler)

    # --- Event emission ---
    def emit(self, event_type: str, **payload) -> GAEvent:
        """
        Push event into queue. Non-blocking - if queue full, last event dropped.
        Returns the created event (or None if dropped).
        
        In cyberboss: this.runtimeEventChain = this.runtimeEventChain.then(...)
        In GA:       queue.put() for later batch processing via process_all()
        """
        event = GAEvent(type=event_type, payload=payload, timestamp=time.time(),
                        turn=payload.get("turn", 0))
        self._emit_count += 1
        try:
            self._queue.put_nowait(event)
        except queue.Full:
            # Drop oldest to make room (ring-buffer behavior for critical events)
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(event)
            except queue.Empty:
                pass
        return event

    # --- Processing ---
    def process_all(self, max_events: int = 500) -> List[GAEvent]:
        """
        Drain and process all queued events, maintaining order.
        Each handler runs with **exception isolation** — a handler failure
        does NOT block other handlers or subsequent events.
        
        This is the key cyberboss pattern:
          .catch(() => {}) → swallow exceptions, continue chain
        
        Returns list of processed events.
        """
        processed = []
        count = 0
        while not self._queue.empty() and count < max_events:
            count += 1
            try:
                event = self._queue.get_nowait()
            except queue.Empty:
                break

            # --- Exception-isolated handler dispatch ---
            # Global (*) handlers first, then type-specific
            all_handlers = (self._handlers.get("*", []) +
                           self._handlers.get(event.type, []))
            for handler in all_handlers:
                try:
                    handler(event)
                except Exception:
                    # cyberboss equivalent: .catch(() => {})
                    logger.debug(f"Pipeline handler for {event.type} failed, continuing",
                                 exc_info=True)

            # Append to history ring buffer
            self._history.append(event)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history // 2:]

            processed.append(event)

        return processed

    def process_one(self) -> Optional[GAEvent]:
        """Process exactly one event from the queue. Returns event or None."""
        results = self.process_all(max_events=1)
        return results[0] if results else None

    # --- Diagnostics ---
    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    def last_events(self, n: int = 10) -> List[GAEvent]:
        return self._history[-n:]

    def summary(self) -> str:
        """Return a human-readable summary of recent events."""
        lines = []
        for e in self._history[-15:]:
            lines.append(f"  [{e.turn}] {e.type}: {list(e.payload.keys())}")
        return "\n".join(lines) if lines else "  (no events)"


# ============================================================
# Watchdog Manager (cyberboss scheduleRuntimeEventWatchdog pattern)
# ============================================================

class WatchdogManager:
    """
    Multi-level timeout watchdog for tool and LLM calls.
    
    Pattern from cyberboss:
      scheduleRuntimeEventWatchdog({ bindingKey, ... })
        → setTimeout(notifyUser, 8s)
        → setTimeout(markFailed, 45s)
      clearRuntimeEventWatchdog(threadId)
        → clearTimeout(notifyTimer)
        → clearTimeout(failureTimer)
    
    GA adaptation: threading.Timer for each level.
    Level 1 (warning): injects prompt note, allows continuation
    Level 2 (failure): marks operation as timed out, triggers recovery
    """
    def __init__(self, pipeline: Optional[EventPipeline] = None):
        self._timers: Dict[str, Dict[str, Any]] = {}  # key -> {'warning':Timer, 'failure':Timer, 'start':float}
        self._lock = threading.Lock()
        self._pipeline = pipeline
        # Callbacks that can be set externally
        self.on_warning: Optional[Callable[[str, float], None]] = None  # (key, elapsed)
        self.on_failure: Optional[Callable[[str, float], None]] = None  # (key, elapsed)
        self._warning_injected: Dict[str, bool] = {}  # key -> already warned

    def schedule(self, key: str, warning_s: float = 8.0, failure_s: float = 45.0,
                 context: Optional[Dict[str, Any]] = None) -> None:
        """
        Schedule multi-level watchdog for an operation identified by key.
        
        - After warning_s: emit watchdog_warning event + call on_warning
        - After failure_s: emit watchdog_timeout event + call on_failure + cancel key
        
        Thread-safe: can be called from any thread.
        """
        with self._lock:
            self.cancel(key)  # clear any existing timers for this key
            entry = {
                'start': time.time(),
                'context': context or {},
                'warning_s': warning_s,
                'failure_s': failure_s,
            }
            self._warning_injected[key] = False

            # Level 1: Warning timer
            if warning_s > 0:
                wt = threading.Timer(warning_s, self._fire_warning, args=[key])
                wt.daemon = True
                wt.start()
                entry['warning'] = wt

            # Level 2: Failure timer
            if failure_s > 0:
                ft = threading.Timer(failure_s, self._fire_failure, args=[key])
                ft.daemon = True
                ft.start()
                entry['failure'] = ft

            self._timers[key] = entry

    def cancel(self, key: str) -> bool:
        """
        Cancel all watchdog timers for key.
        Returns True if there was an active watchdog to cancel.
        """
        with self._lock:
            entry = self._timers.pop(key, None)
            if entry is None:
                return False
            for timer_key in ('warning', 'failure'):
                t = entry.get(timer_key)
                if t is not None:
                    t.cancel()
            self._warning_injected.pop(key, None)
            return True

    def get_elapsed(self, key: str) -> Optional[float]:
        """Return elapsed seconds since watchdog started, or None if not found."""
        entry = self._timers.get(key)
        if entry:
            return time.time() - entry['start']
        return None

    def is_active(self, key: str) -> bool:
        return key in self._timers

    def active_keys(self) -> List[str]:
        with self._lock:
            return list(self._timers.keys())

    def shutdown(self) -> None:
        """Cancel all timers. Call during cleanup."""
        with self._lock:
            for key in list(self._timers.keys()):
                self.cancel(key)

    # --- Internal ---
    def _fire_warning(self, key: str) -> None:
        """Level 1 warning: operation taking longer than expected."""
        elapsed = self.get_elapsed(key)
        if elapsed is None:
            return  # already cancelled
        self._warning_injected[key] = True

        if self._pipeline:
            self._pipeline.emit("watchdog_warning", key=key, elapsed=elapsed)

        if self.on_warning:
            try:
                self.on_warning(key, elapsed)
            except Exception:
                logger.debug("Watchdog warning callback failed", exc_info=True)

    def _fire_failure(self, key: str) -> None:
        """Level 2 failure: operation timed out completely."""
        elapsed = self.get_elapsed(key)
        if elapsed is None:
            return  # already cancelled

        if self._pipeline:
            self._pipeline.emit("watchdog_timeout", key=key, elapsed=elapsed)

        if self.on_failure:
            try:
                self.on_failure(key, elapsed)
            except Exception:
                logger.debug("Watchdog failure callback failed", exc_info=True)

        # Clean up
        self.cancel(key)


# ============================================================
# Integration helpers for GenericAgentHandler
# ============================================================

class GAWatchdogIntegration:
    """
    Drop-in integration of Watchdog + EventPipeline into GenericAgentHandler.
    
    Usage in GenericAgentHandler.__init__:
        self.pipeline = EventPipeline()
        self.watchdog = WatchdogManager(pipeline=self.pipeline)
        self._wd_integration = GAWatchdogIntegration(self)

    Then in the tool call flow:
        self._wd_integration.wrap_tool_call(tool_name, args, tool_call_fn)
    """
    def __init__(self, handler):
        self.handler = handler  # GenericAgentHandler instance
        self.pipeline: EventPipeline = handler.pipeline
        self.watchdog: WatchdogManager = handler.watchdog
        self._turn = 0
        self._tool_warning_injected_this_turn: set = set()

    # --- Turn lifecycle ---
    def on_turn_start(self, turn: int) -> None:
        """Called at the beginning of each LLM turn."""
        self._turn = turn
        self._tool_warning_injected_this_turn.clear()
        self.pipeline.emit("turn_start", turn=turn)

    def on_turn_end(self, turn: int) -> None:
        """Called at the end of each LLM turn."""
        # Cancel any lingering watchdogs
        for key in list(self.watchdog.active_keys()):
            self.watchdog.cancel(key)
        self.pipeline.emit("turn_end", turn=turn)
        self.pipeline.process_all()

    # --- Tool call wrapping ---
    def wrap_tool_call(self, tool_name: str, tool_fn: Callable) -> Any:
        """
        Execute a tool call with watchdog monitoring and event pipeline.
        
        Flow:
          1. emit tool_start event
          2. schedule watchdog (warning=8s, failure=45s or tool-specific timeout)
          3. call tool_fn()
          4. on success: emit tool_end, cancel watchdog
          5. on error: emit tool_error, cancel watchdog
        
        The watchdog warning injects a system prompt note asking the LLM to
        handle the slow tool call gracefully.
        """
        key = f"tool_{tool_name}_{self._turn}_{time.monotonic_ns()}"

        # Determine timeouts - use tool-specific if available
        warning_s = 8.0
        failure_s = min(45.0, getattr(self.handler, 'tool_timeout_default', 60))

        self.pipeline.emit("tool_start", tool=tool_name, key=key, turn=self._turn)

        # Watchdog callbacks for prompt injection
        def on_tool_warning(k: str, elapsed: float) -> None:
            if tool_name not in self._tool_warning_injected_this_turn:
                self._tool_warning_injected_this_turn.add(tool_name)
                # Store warning so turn_end_callback can inject into next_prompt
                wd_warnings = getattr(self.handler, '_wd_warnings', [])
                wd_warnings.append(f"[Watchdog] [WARN]️ 工具 `{tool_name}` 已运行 {elapsed:.0f}s，仍在执行中...")
                self.handler._wd_warnings = wd_warnings

        def on_tool_failure(k: str, elapsed: float) -> None:
            wd_failures = getattr(self.handler, '_wd_failures', [])
            wd_failures.append(f"[Watchdog] [FAIL] 工具 `{tool_name}` 超时({elapsed:.0f}s)，已标记失败")
            self.handler._wd_failures = wd_failures

        self.watchdog.on_warning = on_tool_warning
        self.watchdog.on_failure = on_tool_failure

        self.watchdog.schedule(key, warning_s=warning_s, failure_s=failure_s,
                               context={'tool': tool_name, 'turn': self._turn})

        try:
            result = tool_fn()
            self.pipeline.emit("tool_end", tool=tool_name, key=key, turn=self._turn, success=True)
            return result
        except Exception as e:
            self.pipeline.emit("tool_error", tool=tool_name, key=key, turn=self._turn,
                              error=str(e)[:200])
            raise
        finally:
            self.watchdog.cancel(key)


# ============================================================
# Prompt injection helpers
# ============================================================

def get_watchdog_prompt_injection(handler) -> str:
    """
    Called by turn_end_callback to inject watchdog warnings/failures
    into the next_prompt sent to the LLM.
    
    cyberboss equivalent: sendText to channel with notification message
    """
    parts = []
    wd_warnings = getattr(handler, '_wd_warnings', [])
    wd_failures = getattr(handler, '_wd_failures', [])

    for w in wd_warnings:
        parts.append(f"{w}\n[System] 工具执行较慢，请耐心等待或考虑切换到备用方案。")

    for f in wd_failures:
        parts.append(f"{f}\n[System] 工具执行超时。请使用备用方案重试，或向用户说明情况。")

    # Clear after reading
    handler._wd_warnings = []
    handler._wd_failures = []

    return "\n".join(parts) if parts else ""


# ============================================================
# Self-test / usage example
# ============================================================

if __name__ == "__main__":
    print("=== GA Pipeline Self-Test ===\n")

    # Test EventPipeline
    print("1. EventPipeline basic test...")
    ep = EventPipeline()
    received = []

    ep.on("tool_start", lambda e: received.append(f"START:{e.payload['tool']}"))
    ep.on("tool_end", lambda e: received.append(f"END:{e.payload['tool']}"))
    ep.on("*", lambda e: received.append(f"GLOBAL:{e.type}"))

    ep.emit("tool_start", tool="code_run")
    ep.emit("tool_end", tool="code_run", success=True)
    ep.process_all()
    print(f"   Received: {received}")
    assert "START:code_run" in received
    assert "END:code_run" in received
    assert len(received) == 4  # 2 type-specific + 2 global
    print("   [OK] EventPipeline works\n")

    # Test Watchdog
    print("2. WatchdogManager basic test...")
    wm = WatchdogManager(pipeline=ep)
    warned = []
    failed = []

    wm.on_warning = lambda k, e: warned.append(k)
    wm.on_failure = lambda k, e: failed.append(k)

    wm.schedule("test_op", warning_s=0.1, failure_s=0.3)
    time.sleep(0.2)
    assert "test_op" in warned, f"Expected warning for test_op, got {warned}"
    time.sleep(0.2)
    assert "test_op" in failed, f"Expected failure for test_op, got {failed}"
    print(f"   Warned: {warned}, Failed: {failed}")
    print("   [OK] WatchdogManager works\n")

    # Test cancel before fire
    print("3. WatchdogManager cancel test...")
    wm.schedule("cancelled_op", warning_s=99, failure_s=999)
    cancelled = wm.cancel("cancelled_op")
    assert cancelled
    assert not wm.is_active("cancelled_op")
    print("   [OK] Cancel works\n")

    # Test GAWatchdogIntegration prompt injection
    print("4. Prompt injection test...")
    class MockHandler:
        _wd_warnings = ["[Watchdog] [WARN]️ 工具 `web_scan` 已运行 8s"]
        _wd_failures = []

    prompt = get_watchdog_prompt_injection(MockHandler())
    assert "web_scan" in prompt
    assert MockHandler._wd_warnings == []  # cleared after read
    print(f"   Prompt: {prompt[:80]}...")
    print("   [OK] Prompt injection works\n")

    print("=== ALL TESTS PASSED ===")
