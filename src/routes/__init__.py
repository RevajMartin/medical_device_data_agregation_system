from .admin import router as admin_router
from .aggregations import router as aggregations_router
from .devices import router as devices_router
from .ingestion import router as ingestion_router
from .patients import router as patients_router

__all__ = [
    "ingestion_router",
    "devices_router",
    "aggregations_router",
    "patients_router",
    "admin_router",
]
