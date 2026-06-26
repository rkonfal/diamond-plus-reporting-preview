import importlib.util
import sys
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts"
MODULE_PATH = SCRIPT_DIR / "refresh_data.py"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

SPEC = importlib.util.spec_from_file_location("refresh_data", MODULE_PATH)
refresh_data = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(refresh_data)

PRAGUE_TZ = ZoneInfo("Europe/Prague")


class RefreshDataBuilderTests(unittest.TestCase):
    def test_build_combined_product_views_merges_aliases_and_flags_mismatch(self):
        ctx = refresh_data.CombinedProductsBuildContext(
            wpj_products=[
                {
                    "code": "SKU1",
                    "title": "Alpha Serum",
                    "ean": "111",
                    "url": "/alpha-serum",
                    "visible": True,
                    "price": {"withVat": 499},
                    "stores": [
                        {"store": {"id": 1, "name": "4PX Praha"}, "inStore": 20},
                        {"store": {"id": 2, "name": "Praha"}, "inStore": 5},
                    ],
                }
            ],
            yesterday_orders=[
                {
                    "dateCreated": "2026-06-25T10:00:00+02:00",
                    "deliveryAddress": {"country": {"code": "CZ"}},
                    "source": {"name": "eshop"},
                    "items": [
                        {
                            "type": "product",
                            "code": "SKU1",
                            "name": "Alpha Serum",
                            "pieces": 3,
                            "totalPrice": {"withVat": 1497},
                        }
                    ],
                }
            ],
            cz_inventory={
                "items": [
                    {
                        "sku_code": "SKU1/RED",
                        "sku_id": "A1",
                        "batch_no": "B1",
                        "available_stock": 7,
                        "pending_stock": 1,
                        "freeze_stock": 0,
                        "onway_stock": 2,
                    }
                ]
            },
            sk_inventory={
                "items": [
                    {
                        "sku_code": "SKU1",
                        "sku_id": "A2",
                        "batch_no": "B2",
                        "available_stock": 2,
                        "pending_stock": 0,
                        "freeze_stock": 1,
                        "onway_stock": 0,
                    }
                ]
            },
            cz_outbound={
                "items": [
                    {
                        "consignment_no": "CZ-1",
                        "logistics_product_code": "L1",
                        "carrier_brand_name": "DHL",
                        "create_time": "2026-06-25T12:00:00+02:00",
                        "outboundlist_sku": [
                            {"sku_code": "SKU1/RED", "sku_name": "Alpha Serum", "qty": 4}
                        ],
                    }
                ]
            },
            sk_outbound={"items": []},
            start_dt=datetime(2026, 6, 25, 0, 0, 1, tzinfo=PRAGUE_TZ),
            end_dt=datetime(2026, 6, 25, 23, 59, 59, tzinfo=PRAGUE_TZ),
            generated_at="2026-06-26T10:00:00+02:00",
            manual_overrides={"aliases": {}, "ignore": set()},
            pos_admin_views={},
        )

        combined_index, combined_overview = refresh_data.build_combined_product_views(ctx)

        self.assertEqual(combined_index["counts"]["allCodes"], 1)
        self.assertEqual(combined_index["counts"]["stockMismatch"], 1)
        self.assertEqual(combined_index["counts"]["lowAfterSales"], 1)
        self.assertEqual(combined_index["counts"]["autoMapped4pxAliases"], 1)

        item = combined_index["items"][0]
        self.assertEqual(item["fourpx"]["availableTotal"], 9.0)
        self.assertEqual(item["yesterdaySales"]["units"], 3.0)
        self.assertEqual(item["yesterdayOutbound"]["czUnits"], 4.0)
        self.assertEqual(item["yesterdayOutbound"]["shipments"], 1)
        self.assertIn("stock_mismatch", item["flags"])
        self.assertIn("low_after_sales", item["flags"])
        self.assertIn("auto_mapped_4px_alias", item["flags"])
        self.assertEqual(combined_overview["priorityShortlist"][0]["code"], "SKU1")

    def test_build_inventory_analytics_730d_computes_cover_and_reorder_metrics(self):
        combined_index = {
            "items": [
                {
                    "code": "SKU1",
                    "title": "Alpha Serum",
                    "wpj": {
                        "fourpxStoreTotal": 4,
                        "totalStore": 6,
                        "priceWithVat": 499,
                    },
                    "fourpx": {"availableTotal": 1},
                }
            ]
        }
        ctx = refresh_data.InventoryAnalyticsBuildContext(
            combined_index=combined_index,
            orders=[
                {
                    "dateCreated": "2026-06-20T10:00:00+02:00",
                    "deliveryAddress": {"country": {"code": "CZ"}},
                    "items": [{"type": "product", "code": "SKU1", "name": "Alpha Serum", "pieces": 9}],
                },
                {
                    "dateCreated": "2025-12-31T10:00:00+02:00",
                    "deliveryAddress": {"country": {"code": "SK"}},
                    "items": [{"type": "product", "code": "SKU1", "name": "Alpha Serum", "pieces": 30}],
                },
                {
                    "dateCreated": "2025-01-10T10:00:00+02:00",
                    "deliveryAddress": {"country": {"code": "CZ"}},
                    "items": [{"type": "product", "code": "SKU1", "name": "Alpha Serum", "pieces": 100}],
                },
            ],
            start_dt=datetime(2025, 1, 1, 0, 0, 0, tzinfo=PRAGUE_TZ),
            end_dt=datetime(2026, 6, 26, 23, 59, 59, tzinfo=PRAGUE_TZ),
            generated_at="2026-06-26T10:00:00+02:00",
            wpj_by_code={"SKU1": {"title": "Alpha Serum"}},
            manual_overrides={"aliases": {}, "ignore": set()},
            pos_admin_views={},
            ordering_reference_overrides={},
            ordering_packaging_map={},
        )

        payload = refresh_data.build_inventory_analytics_730d(ctx)

        self.assertEqual(payload["summary"]["trackedItems"], 1)
        self.assertEqual(payload["summary"]["criticalReorderItems"], 1)
        self.assertEqual(payload["summary"]["greenTurnoverItems"], 1)
        self.assertEqual(payload["summary"]["fastLowCoverItems"], 1)

        item = payload["items"][0]
        self.assertEqual(item["units730d"], 139.0)
        self.assertEqual(item["units365d"], 39.0)
        self.assertEqual(item["units90d"], 9.0)
        self.assertEqual(item["turnoverZone"], "green")
        self.assertEqual(item["reorderRisk"], "critical")
        self.assertEqual(item["recommendedOrderUnits"], 6)
        self.assertIn("reorder_candidate", item["tags"])
        self.assertIn("fast_mover_low_cover", item["tags"])
        self.assertEqual(item["byView"]["sk"]["units365d"], 30.0)

    def test_build_ordering_core_prioritizes_critical_and_overstocked_items(self):
        analytics_payload = {
            "window": {"days": 548},
            "items": [
                {
                    "code": "TOP-1",
                    "title": "Critical Serum",
                    "orderable": True,
                    "orderingRole": "top_sku",
                    "reorderRisk": "critical",
                    "turnoverZone": "green",
                    "daysOfCover90d": 5,
                    "daysOfCover365d": 9.1,
                    "effectiveStock": 2,
                    "recommendedOrderUnits": 20,
                    "recommendedMinUnits": 8,
                    "units90d": 40,
                    "units365d": 180,
                    "units30d": 15,
                    "dailyRunRate90d": 0.444,
                    "dailyRunRate365d": 0.493,
                    "dailyRunRate730d": 0.4,
                    "trend90v365Pct": 30,
                    "stockValueSelling": 1000,
                    "unitSellingPrice": 50,
                    "orderPackOptions": [],
                    "sourceChannel": "unknown",
                },
                {
                    "code": "WATCH-1",
                    "title": "Watch Serum",
                    "orderable": True,
                    "orderingRole": "top_sku",
                    "reorderRisk": "watch",
                    "turnoverZone": "orange",
                    "daysOfCover90d": 40,
                    "daysOfCover365d": 55,
                    "effectiveStock": 12,
                    "recommendedOrderUnits": 5,
                    "recommendedMinUnits": 2,
                    "units90d": 20,
                    "units365d": 90,
                    "units30d": 8,
                    "dailyRunRate90d": 0.222,
                    "dailyRunRate365d": 0.247,
                    "dailyRunRate730d": 0.2,
                    "trend90v365Pct": 5,
                    "stockValueSelling": 600,
                    "unitSellingPrice": 40,
                    "orderPackOptions": [],
                    "sourceChannel": "unknown",
                },
                {
                    "code": "RED-1",
                    "title": "Overstock Cream",
                    "orderable": True,
                    "orderingRole": "fill_up",
                    "reorderRisk": "none",
                    "turnoverZone": "red",
                    "daysOfCover90d": 400,
                    "daysOfCover365d": 420,
                    "effectiveStock": 300,
                    "recommendedOrderUnits": 0,
                    "recommendedMinUnits": 0,
                    "units90d": 10,
                    "units365d": 50,
                    "units30d": 3,
                    "dailyRunRate90d": 0.111,
                    "dailyRunRate365d": 0.137,
                    "dailyRunRate730d": 0.1,
                    "trend90v365Pct": -40,
                    "stockValueSelling": 5000,
                    "unitSellingPrice": 20,
                    "orderPackOptions": [],
                    "sourceChannel": "unknown",
                },
                {
                    "code": "FILL-1",
                    "title": "Fill Up Oil",
                    "orderable": True,
                    "orderingRole": "fill_up",
                    "reorderRisk": "watch",
                    "turnoverZone": "green",
                    "daysOfCover90d": 45,
                    "daysOfCover365d": 70,
                    "effectiveStock": 18,
                    "recommendedOrderUnits": 8,
                    "recommendedMinUnits": 4,
                    "units90d": 25,
                    "units365d": 120,
                    "units30d": 10,
                    "dailyRunRate90d": 0.278,
                    "dailyRunRate365d": 0.329,
                    "dailyRunRate730d": 0.25,
                    "trend90v365Pct": 8,
                    "stockValueSelling": 900,
                    "unitSellingPrice": 45,
                    "orderPackOptions": [],
                    "sourceChannel": "unknown",
                },
            ],
        }

        payload = refresh_data.build_ordering_core(analytics_payload, "2026-06-26T10:00:00+02:00")

        self.assertEqual(payload["summary"]["trackedItems"], 4)
        self.assertEqual(payload["summary"]["criticalReorderItems"], 1)
        self.assertEqual(payload["summary"]["watchReorderItems"], 2)
        self.assertEqual(payload["summary"]["redTurnoverItems"], 1)
        self.assertEqual(payload["criticalReorder"][0]["code"], "TOP-1")
        self.assertEqual(payload["overstockRisks"][0]["code"], "RED-1")
        self.assertEqual(payload["suggestedFillers"][0]["code"], "FILL-1")
        self.assertTrue(any("kritické pokrytí" in alert for alert in payload["alerts"]))
        self.assertTrue(any("červené obrátkovosti" in alert for alert in payload["alerts"]))

    def test_build_morning_report_keeps_context_and_calculates_deltas(self):
        ctx = refresh_data.MorningReportBuildContext(
            report_date=date(2026, 6, 25),
            wpj_summary={"orders": 12, "revenueWithVat": 2400},
            baseline_orders=10,
            baseline_revenue=2000,
            stock_summary={"lowStockSoldYesterday": [], "negativeStoreStock": []},
            inventory_summary={"health": "ok"},
            logistics_summary={"shipmentsTotal": 8, "coverageWarnings": [], "expiringProducts": []},
            alerts=["alert"],
            priorities=["priority"],
            warnings=["warning"],
            mtd_summary={"orders": 100},
            inventory_health={"aCriticalCount": 2, "topRiskCodes": ["SKU1"], "slowDeadShare": 10},
        )
        fixed_now = datetime(2026, 6, 26, 10, 30, 0, tzinfo=PRAGUE_TZ)

        with patch.object(refresh_data, "current_local_time", return_value=fixed_now):
            payload = refresh_data.build_morning_report(ctx)

        self.assertEqual(payload["generatedAt"], fixed_now.isoformat())
        self.assertEqual(payload["reportDate"], "2026-06-25")
        self.assertEqual(payload["quickSummary"]["orders"]["deltaPct"], 20.0)
        self.assertEqual(payload["quickSummary"]["revenueWithVat"]["deltaPct"], 20.0)
        self.assertEqual(payload["quickSummary"]["shipmentsTotal"], 8)
        self.assertEqual(payload["priorities"], ["priority"])
        self.assertEqual(payload["warnings"], ["warning"])
        self.assertEqual(payload["window"]["from"], "2026-06-25T00:00:01+02:00")
        self.assertEqual(payload["window"]["to"], "2026-06-25T23:59:59+02:00")


if __name__ == "__main__":
    unittest.main()
