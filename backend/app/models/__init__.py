import math
from typing import Any

from pydantic import BaseModel, Field, field_validator


class PredictionRequest(BaseModel):
    address: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    features: dict[str, float | int | None]

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str) -> str:
        return value.lower()

    @field_validator("features")
    @classmethod
    def validate_features(cls, values: dict[str, float | int | None]):
        if not values:
            raise ValueError("features cannot be empty")
        if any(value is not None and not math.isfinite(float(value)) for value in values.values()):
            raise ValueError("features must contain finite numbers or null")
        return values


class PredictionResponse(BaseModel):
    address: str
    fraud_probability: float
    trust_score: float
    risk_action: str
    sequence: int | None = None
    actual_label: int | None = None
    source: str = "api"
    features: dict[str, Any] | None = None
    explanation: list[dict[str, Any]] | None = None
    model_version: str | None = None
    latency_ms: float | None = None
    anomaly_score: float | None = None


class TransactionScreenRequest(BaseModel):
    sender: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    recipient: str | None = Field(
        default=None, pattern=r"^0x[a-fA-F0-9]{40}$"
    )
    value_eth: float = Field(ge=0, allow_inf_nan=False)
    gas: int = Field(default=21000, ge=21000)
    gas_price_wei: int | None = Field(default=None, ge=0)
    nonce: int | None = Field(default=None, ge=0)
    input: str = Field(default="0x", pattern=r"^0x[a-fA-F0-9]*$")

    @field_validator("sender", "recipient")
    @classmethod
    def normalize_addresses(cls, value: str | None) -> str | None:
        return value.lower() if value else value


class SimulationRequest(BaseModel):
    baseline: TransactionScreenRequest
    scenario: TransactionScreenRequest


class ReviewedLabelRequest(BaseModel):
    transaction_hash: str | None = Field(
        default=None, pattern=r"^(0x)?[a-fA-F0-9]{64}$"
    )
    address: str = Field(pattern=r"^0x[a-fA-F0-9]{40}$")
    label: int = Field(ge=0, le=1)
    reviewer: str = Field(min_length=2, max_length=120)
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("address")
    @classmethod
    def normalize_address(cls, value: str) -> str:
        return value.lower()

    @field_validator("transaction_hash")
    @classmethod
    def normalize_hash(cls, value: str | None) -> str | None:
        if value and not value.startswith("0x"):
            return f"0x{value.lower()}"
        return value.lower() if value else None
