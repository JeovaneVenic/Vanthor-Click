"""Estado de uma regra durante a automação: transforma quadros em "agora é hora de agir".

Não sabe nada de tela nem de cliques: recebe "a cor está/não está" com o horário e devolve sinais.
Por isso é testável com sequências como "1101111".

Ciclo:
  WAITING ─(N quadros com a cor)→ PRESENT ─(agenda)→ SCHEDULED/COOLDOWN ─(hora)→ FIRE
  PRESENT ─(M quadros sem a cor)→ WAITING  (rearma o modo "ao aparecer"; cancela o agendado)
"""

from __future__ import annotations

from enum import StrEnum

from vanthor.rules.models import FireMode, TriggerSettings

_EPS = 1e-9  # somas de 0,1 em ponto flutuante não podem atrasar um disparo em um ciclo


class RuleSignal(StrEnum):
    APPEARED = "appeared"  # cor confirmada (N quadros seguidos)
    DISAPPEARED = "disappeared"  # cor sumiu de verdade (M quadros seguidos)
    FIRE = "fire"  # executar a ação agora
    DELAY_CANCELLED = "delay_cancelled"  # a cor sumiu antes da hora de agir
    LIMIT_REACHED = "limit_reached"  # atingiu o máximo de execuções


class RulePhase(StrEnum):
    WAITING = "waiting"  # sem a cor
    CONFIRMING = "confirming"  # viu a cor, contando quadros
    PRESENT = "present"  # cor confirmada, nada agendado (já agiu nesta aparição)
    SCHEDULED = "scheduled"  # vai agir após a espera configurada
    COOLDOWN = "cooldown"  # vai agir quando o intervalo mínimo acabar
    DONE = "done"  # não age mais nesta execução (disparo único ou limite)


class RuleRuntime:
    def __init__(self, trigger: TriggerSettings) -> None:
        self.trigger = trigger
        self.executions = 0
        self._hits = 0
        self._misses = 0
        self._present = False
        self._armed = True  # modo "ao aparecer": pode agir nesta aparição?
        self._fire_at: float | None = None
        self._fire_reason = RulePhase.SCHEDULED
        self._last_fire: float | None = None
        self._done = False

    @property
    def phase(self) -> RulePhase:
        if self._done:
            return RulePhase.DONE
        if self._fire_at is not None:
            return self._fire_reason
        if self._present:
            return RulePhase.PRESENT
        return RulePhase.CONFIRMING if self._hits else RulePhase.WAITING

    def on_frame(self, now: float, detected: bool) -> list[RuleSignal]:
        if self._done:
            return []
        signals: list[RuleSignal] = []
        if detected:
            self._hits += 1
            self._misses = 0
        else:
            self._misses += 1
            self._hits = 0

        if not self._present and self._hits >= self.trigger.confirm_frames:
            self._present = True
            signals.append(RuleSignal.APPEARED)
            if self._armed:
                self._schedule(now, now + self.trigger.delay_ms / 1000)
        elif self._present and self._misses >= self.trigger.release_frames:
            self._present = False
            if self._fire_at is not None:
                self._fire_at = None
                signals.append(RuleSignal.DELAY_CANCELLED)
            signals.append(RuleSignal.DISAPPEARED)
            self._armed = True  # só agora pode agir de novo no modo "ao aparecer"

        signals += self.poll(now)
        return signals

    def poll(self, now: float) -> list[RuleSignal]:
        """Chamado a cada ciclo do motor: dispara o que estava agendado (mesmo sem quadro novo)."""
        if self._done or self._fire_at is None or now + _EPS < self._fire_at:
            return []
        self._fire_at = None
        self.executions += 1
        self._last_fire = now
        signals = [RuleSignal.FIRE]

        limit = self.trigger.max_executions
        if self.trigger.mode is FireMode.ONCE or (limit and self.executions >= limit):
            self._done = True
            if limit and self.executions >= limit:
                signals.append(RuleSignal.LIMIT_REACHED)
            return signals

        if self.trigger.mode is FireMode.ON_APPEAR:
            self._armed = False
        elif self._present:  # contínuo: próxima repetição enquanto a cor estiver lá
            self._schedule(now, now + self.trigger.cooldown_ms / 1000)
        return signals

    def _schedule(self, now: float, wanted: float) -> None:
        earliest = self._last_fire + self.trigger.cooldown_ms / 1000 if self._last_fire is not None else now
        self._fire_at = max(wanted, earliest)
        self._fire_reason = RulePhase.COOLDOWN if earliest > wanted + _EPS else RulePhase.SCHEDULED
