"""Serve the user-supplied SVG workbench alongside the business API."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

DESIGN_DIR = Path(__file__).resolve().parent.parent / "AIX-UI-SVG-Sources"
APP_ASSETS = dict.fromkeys(("main.js", "trade.js", "orders.js", "supplier.js", "monitor.js", "notifications.js"), "text/javascript")
APP_ASSETS["source.css"] = "text/css"
ARTWORK = {
    "aix_buyer_region_filtered_marketplace.svg",
    "aix_monitor_icon.svg",
    "aix_monitor_icon_active.svg",
    "aix_notifications_icon.svg",
    "aix_notifications_icon_active.svg",
    "aix_product_details_reviews_unified.svg",
    "aix_supplier_messages_unified.svg",
    "aix_supplier_buyer_chat_unified.svg",
    "aix_wallet_v2_01_home.svg",
    "aix_wallet_v2_03_contract_protection.svg",
    "aix_wallet_v2_04_protected_payment.svg",
    "aix_wallet_v2_05_order_control.svg",
    "aix_wallet_v2_06_acceptance_settlement.svg",
    "prototype_screens/prototype_quote_01_phone_01_quote_requests.svg",
    "prototype_screens/prototype_quote_02_phone_02_review_quote.svg",
    "prototype_screens/prototype_supplier_01_01_supplier_home.svg",
    "prototype_screens/prototype_supplier_04_04_my_orders.svg",
    "prototype_screens/prototype_supplier_05_05_order_monitor.svg",
    "prototype_screens/prototype_supplier_06_06_my_products.svg",
    "prototype_screens/prototype_supplier_09_09_notifications.svg",
    "prototype_screens/prototype_supplier_10_10_profile_and_settings.svg",
}
PORTAL_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
        "connect-src 'self'; form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
ASSET_HEADERS = {**PORTAL_HEADERS, "Cache-Control": "no-cache"}
SVG_HEADERS = {**ASSET_HEADERS, "Content-Security-Policy": "sandbox; default-src 'none'; frame-ancestors 'none'"}


def install_portal(app: FastAPI) -> None:
    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    @app.api_route("/source/", methods=["GET", "HEAD"], include_in_schema=False)
    def index():
        page = DESIGN_DIR / "app" / "index.html"
        if not page.is_file():
            raise HTTPException(503, "SVG workbench is unavailable")
        headers = {**PORTAL_HEADERS, "Cache-Control": "no-store"}
        return FileResponse(page, media_type="text/html", headers=headers)

    @app.api_route("/source/app/{name}", methods=["GET", "HEAD"], include_in_schema=False)
    def app_asset(name: str):
        path = DESIGN_DIR / "app" / name
        if name not in APP_ASSETS or not path.is_file():
            raise HTTPException(404, "Asset not found")
        return FileResponse(path, media_type=APP_ASSETS[name], headers=ASSET_HEADERS)

    @app.api_route("/source/design/{name:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def design_asset(name: str):
        path = DESIGN_DIR / name
        if name not in ARTWORK or not path.is_file():
            raise HTTPException(404, "Artwork not found")
        return FileResponse(path, media_type="image/svg+xml", headers=SVG_HEADERS)
