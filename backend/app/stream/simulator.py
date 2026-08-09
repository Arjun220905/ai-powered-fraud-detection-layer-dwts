from __future__ import annotations

import asyncio

import pandas as pd

from app.ml.feature_engineering import clean_columns


class StreamSimulator:
    def __init__(self, dataset_path: str):
        self.frame = clean_columns(pd.read_csv(dataset_path))
        self.position = 0
        self.lock = asyncio.Lock()

    async def next(self, delay_ms: int = 0) -> tuple[int, dict]:
        if not 0 <= delay_ms <= 5000:
            raise ValueError("delay_ms must be between 0 and 5000")
        async with self.lock:
            if self.position >= len(self.frame):
                self.position = 0
            sequence = self.position + 1
            row = self.frame.iloc[self.position].to_dict()
            self.position += 1
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000)
        return sequence, row

    async def reset(self) -> None:
        async with self.lock:
            self.position = 0
