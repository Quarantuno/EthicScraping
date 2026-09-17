"""Revisione umana dei record del dataset prima dell'uso per il training.

I filtri automatici (licenza, PII, dedup) sono una prima difesa, non una
garanzia -- specialmente su fonti "a rischio" (forum, commenti, contenuti
generati dagli utenti). Questo modulo tiene lo stato di una sessione di
revisione (chi ha approvato/rifiutato cosa) su disco, cosi' da poter
interrompere e riprendere, e scrive i record approvati/rifiutati in due
file JSONL separati accanto al dataset originale.

La logica e' separata dall'interfaccia interattiva (vedi il comando
`review` in main.py) apposta per poter essere testata senza input da
tastiera.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional


class ReviewSession:
    def __init__(self, dataset_path: str, review_dir: Optional[str] = None):
        self.dataset_path = Path(dataset_path)
        if not self.dataset_path.exists():
            raise FileNotFoundError(f"Dataset non trovato: {self.dataset_path}")

        base_dir = Path(review_dir) if review_dir else self.dataset_path.parent
        base_dir.mkdir(parents=True, exist_ok=True)
        stem = self.dataset_path.stem

        self.state_path = base_dir / f"{stem}_review_state.json"
        self.approved_path = base_dir / f"{stem}_approved.jsonl"
        self.rejected_path = base_dir / f"{stem}_rejected.jsonl"

        self.records: List[dict] = self._load_records()
        self.state: Dict[str, str] = self._load_state()

    def _load_records(self) -> List[dict]:
        records = []
        with self.dataset_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    def _load_state(self) -> Dict[str, str]:
        if self.state_path.exists():
            with self.state_path.open(encoding="utf-8") as f:
                return json.load(f)
        return {}

    def _save_state(self) -> None:
        with self.state_path.open("w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)

    def pending(self) -> List[dict]:
        """Record non ancora approvati ne' rifiutati in questa o in una
        sessione precedente (lo stato e' persistito su disco)."""
        return [r for r in self.records if r.get("id") not in self.state]

    def stats(self) -> dict:
        approved = sum(1 for v in self.state.values() if v == "approved")
        rejected = sum(1 for v in self.state.values() if v == "rejected")
        return {
            "total": len(self.records),
            "approved": approved,
            "rejected": rejected,
            "pending": len(self.records) - len(self.state),
        }

    def decide(self, record: dict, decision: str) -> None:
        """Registra una decisione e la scrive subito su disco (stato +
        file di destinazione), cosi' che un'interruzione a meta' sessione
        non perda il lavoro gia' fatto.

        Nota: rivalutare un record gia' deciso aggiorna lo stato ma non
        rimuove la scrittura precedente dal file di destinazione originale
        -- per correggere un errore, ripulisci a mano il file interessato.
        """
        if decision not in ("approved", "rejected"):
            raise ValueError(f"Decisione non valida: {decision!r}")

        record_id = record.get("id")
        if record_id is None:
            raise ValueError("Il record non ha un campo 'id'")

        already_decided = self.state.get(record_id)
        self.state[record_id] = decision
        self._save_state()

        if already_decided == decision:
            return  # gia' scritto in precedenza con la stessa decisione

        target = self.approved_path if decision == "approved" else self.rejected_path
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
