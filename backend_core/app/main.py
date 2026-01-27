import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .db import create_db_and_tables  
from .auth import router as auth_router
from .customers import router as customers_router
from .tracking import router as tracking_router
from .queries import router as queries_router
from .instructions import router as instructions_router
from .inventory import router as inventory_router
from .excel import router as excel_router
from .notifications import router as notifications_router
from .users import router as users_router
from .dashboard import router as dashboard_router
from .tracking_api import router as tracking_api_router
from .scrap import router as scrap_router

# New v2 API routers for improved steel industry operations
from .routers.inventory_v2 import router as inventory_v2_router
from .routers.grn import router as grn_router
from .routers.dispatch import router as dispatch_router


def get_cors_origins():
    """Get CORS origins from environment or use defaults for development"""
    origins_env = os.getenv("CORS_ORIGINS", "")
    if origins_env:
        return [o.strip() for o in origins_env.split(",") if o.strip()]
    # Default development origins
    return [
        "http://127.0.0.1:5500",
        "http://localhost:5500",
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:8080",
        "http://localhost:8080",
    ]


def create_app() -> FastAPI:
    app = FastAPI(
        title="KumarBrothers Steel Industry ERP",
        description="Inventory Management System for Steel Industry with full traceability",
        version="2.0.0"
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Legacy v1 routers (for backward compatibility)
    app.include_router(auth_router)
    app.include_router(notifications_router)
    app.include_router(users_router)
    app.include_router(customers_router)
    app.include_router(tracking_router)
    app.include_router(queries_router)
    app.include_router(instructions_router)
    app.include_router(inventory_router)
    app.include_router(excel_router)
    app.include_router(dashboard_router)
    app.include_router(tracking_api_router)
    app.include_router(scrap_router)
    
    # New v2 routers (improved steel industry operations)
    app.include_router(inventory_v2_router)
    app.include_router(grn_router)
    app.include_router(dispatch_router)

    @app.on_event("startup")
    def on_startup():
        print("[backend_core] Creating database tables at startup...")
        create_db_and_tables()
        # Also create v2 tables
        from .models_v2 import Base as BaseV2
        from .db import engine
        BaseV2.metadata.create_all(bind=engine)
        print("[backend_core] Database ready (v1 + v2 tables).")

    return app


app = create_app()
