"""
Request validation models and input sanitization utilities for the F1 Points Calculator API.
"""

from pydantic import BaseModel, Field, field_validator, model_validator
from typing import List, Optional, Literal
import re


# ============================================================================
# Constants
# ============================================================================

MIN_SEASON_YEAR = 1950  # First F1 season
MAX_SEASON_YEAR = 2030  # Reasonable upper limit
MAX_CUSTOM_POINTS_LENGTH = 30  # Max positions to award points
MAX_DRIVER_IDS = 50  # Max drivers in a selection


# ============================================================================
# Utility Functions
# ============================================================================

def sanitize_string(value: str, max_length: int = 200) -> str:
    """Sanitize string input by removing potentially dangerous characters."""
    if not value:
        return value
    # Remove any HTML/script tags
    value = re.sub(r'<[^>]*>', '', value)
    # Remove null bytes
    value = value.replace('\x00', '')
    # Truncate to max length
    return value[:max_length].strip()


def validate_positive_int(value: int, field_name: str, min_val: int = 1, max_val: int = 999999) -> int:
    """Validate that an integer is within expected bounds."""
    if value < min_val or value > max_val:
        raise ValueError(f"{field_name} must be between {min_val} and {max_val}")
    return value


# ============================================================================
# Request Models
# ============================================================================

class StandingsRequest(BaseModel):
    """Request model for calculating standings."""
    season_year: int = Field(
        ...,
        ge=MIN_SEASON_YEAR,
        le=MAX_SEASON_YEAR,
        description="The F1 season year to calculate standings for"
    )
    points_system: Optional[List[int]] = Field(
        default=None,
        max_length=MAX_CUSTOM_POINTS_LENGTH,
        description="Custom points system array (e.g., [25, 18, 15, 12, 10, 8, 6, 4, 2, 1])"
    )
    selected_driver_ids: Optional[List[int]] = Field(
        default=None,
        max_length=MAX_DRIVER_IDS,
        description="Optional list of driver IDs to filter results"
    )

    @field_validator('points_system')
    @classmethod
    def validate_points_system(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        if v is None:
            return v
        if len(v) == 0:
            raise ValueError("Points system cannot be empty if provided")
        for i, pts in enumerate(v):
            if pts < 0:
                raise ValueError(f"Points at position {i+1} cannot be negative")
            if pts > 1000:
                raise ValueError(f"Points at position {i+1} exceeds maximum of 1000")
        return v

    @field_validator('selected_driver_ids')
    @classmethod
    def validate_driver_ids(cls, v: Optional[List[int]]) -> Optional[List[int]]:
        if v is None:
            return v
        for driver_id in v:
            if driver_id < 1:
                raise ValueError("Driver IDs must be positive integers")
        return list(set(v))  # Remove duplicates


class SimulateSeasonRequest(BaseModel):
    """Request model for season simulation with AI."""
    season_year: int = Field(
        ...,
        ge=MIN_SEASON_YEAR,
        le=MAX_SEASON_YEAR,
        description="The F1 season year to simulate"
    )
    points_system: Optional[List[int]] = Field(
        default=None,
        max_length=MAX_CUSTOM_POINTS_LENGTH,
        description="Custom points system array"
    )
    gemini_api_key: Optional[str] = Field(
        default=None,
        min_length=10,
        max_length=200,
        description="Gemini API key for AI generation (should be provided server-side in production)"
    )

    @field_validator('gemini_api_key')
    @classmethod
    def validate_api_key(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        # Basic format check - should look like an API key
        v = v.strip()
        if not re.match(r'^[A-Za-z0-9_-]+$', v):
            raise ValueError("Invalid API key format")
        return v


class RaceResultsRequest(BaseModel):
    """Request model for fetching race results."""
    season_year: int = Field(
        ...,
        ge=MIN_SEASON_YEAR,
        le=MAX_SEASON_YEAR,
        description="The F1 season year"
    )
    race_number: Optional[int] = Field(
        default=None,
        ge=1,
        le=30,
        description="The race number/round within the season"
    )
    race_id: Optional[int] = Field(
        default=None,
        ge=1,
        description="Internal database race ID"
    )

    @model_validator(mode='after')
    def check_at_least_one_id(self):
        if self.race_number is None and self.race_id is None:
            raise ValueError("Either race_number or race_id must be provided")
        return self


class HeadToHeadRequest(BaseModel):
    """Request model for head-to-head comparison."""
    driver1_id: int = Field(..., ge=1, description="First driver ID")
    driver2_id: int = Field(..., ge=1, description="Second driver ID")
    season: Optional[int] = Field(
        default=None,
        ge=MIN_SEASON_YEAR,
        le=MAX_SEASON_YEAR,
        description="Season year for comparison (None for career comparison)"
    )
    mode: Literal['season', 'career'] = Field(
        default='season',
        description="Comparison mode: 'season' or 'career'"
    )

    @model_validator(mode='after')
    def validate_different_drivers(self):
        if self.driver1_id == self.driver2_id:
            raise ValueError("Cannot compare a driver with themselves")
        return self


class DriverQueryParams(BaseModel):
    """Query parameters for driver endpoints."""
    season: Optional[int] = Field(
        default=None,
        ge=MIN_SEASON_YEAR,
        le=MAX_SEASON_YEAR,
        description="Filter drivers by season"
    )


class RaceQueryParams(BaseModel):
    """Query parameters for race endpoints."""
    season: int = Field(
        ...,
        ge=MIN_SEASON_YEAR,
        le=MAX_SEASON_YEAR,
        description="Season year to get races for"
    )


# ============================================================================
# Response Models
# ============================================================================

class HealthResponse(BaseModel):
    """Response model for health check endpoint."""
    status: Literal['healthy', 'degraded', 'unhealthy']
    version: str
    database: Literal['connected', 'disconnected']
    cache: Literal['connected', 'disconnected', 'disabled']
    timestamp: str


class ErrorResponse(BaseModel):
    """Standard error response model."""
    error: str
    detail: Optional[str] = None
    status_code: int
    path: Optional[str] = None
    timestamp: str


class SeasonResponse(BaseModel):
    """Response model for seasons list."""
    seasons: List[int]


class DriverInfo(BaseModel):
    """Driver information model."""
    driverId: int
    forename: str
    surname: str


class DriversResponse(BaseModel):
    """Response model for drivers list."""
    drivers: List[DriverInfo]


class RaceInfo(BaseModel):
    """Race information model."""
    raceId: int
    name: str
    round: Optional[int] = None


class RacesResponse(BaseModel):
    """Response model for races list."""
    races: List[RaceInfo]


class PointsSystemInfo(BaseModel):
    """Points system information."""
    name: str
    points: List[int]


class PointsSystemsResponse(BaseModel):
    """Response model for available points systems."""
    points_systems: dict


# ============================================================================
# Validation Helpers
# ============================================================================

class InputValidator:
    """Static class for input validation utilities."""

    @staticmethod
    def validate_season_range(start_year: int, end_year: int) -> tuple[int, int]:
        """Validate a season range."""
        if start_year > end_year:
            raise ValueError("Start year cannot be after end year")
        if end_year - start_year > 50:
            raise ValueError("Season range cannot exceed 50 years")
        return start_year, end_year

    @staticmethod
    def validate_race_id(race_id: int) -> int:
        """Validate a race ID."""
        if race_id < 1 or race_id > 999999:
            raise ValueError("Invalid race ID")
        return race_id

    @staticmethod
    def validate_constructor_id(constructor_id: int) -> int:
        """Validate a constructor ID."""
        if constructor_id < 1 or constructor_id > 99999:
            raise ValueError("Invalid constructor ID")
        return constructor_id

    @staticmethod
    def is_safe_filename(filename: str) -> bool:
        """Check if a filename is safe (no path traversal)."""
        if not filename:
            return False
        # Check for path traversal attempts
        if '..' in filename or '/' in filename or '\\' in filename:
            return False
        # Check for null bytes
        if '\x00' in filename:
            return False
        # Only allow alphanumeric, underscore, hyphen, and dot
        return bool(re.match(r'^[\w\-. ]+$', filename))
