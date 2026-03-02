from .start import router as start_router
from .photo import router as photo_router
from .admin import router as admin_router

__all__ = ["start_router", "photo_router", "admin_router"]
