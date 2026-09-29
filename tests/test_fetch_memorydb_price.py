import json
import os
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "fetch_memorydb_price.sh"

# A realistic AWS Price List product: Reserved (upfront fee, $0) appears
# before OnDemand in document order, exactly the shape that made the old
# unscoped `.. | objects | .USD?` walk pick the wrong term.
_RESERVED_BEFORE_ONDEMAND_PRODUCT = json.dumps({
    "product": {
        "productFamily": "Cache Instance",
        "attributes": {
            "instanceType": "db.r6g.large",
            "location": "US East (N. Virginia)",
        },
        "sku": "ABC123",
    },
    "serviceCode": "AmazonMemoryDB",
    "terms": {
        "Reserved": {
            "ABC123.RESRATE.NQ3QZPMQV9": {
                "priceDimensions": {
                    "ABC123.RESRATE.NQ3QZPMQV9.6YS6EN2CT7": {
                        "unit": "Quantity",
                        "description": "Upfront Fee",
                        "pricePerUnit": {"USD": "0.0000000000"},
                    }
                }
            }
        },
        "OnDemand": {
            "ABC123.JRTCKXETXF": {
                "priceDimensions": {
                    "ABC123.JRTCKXETXF.6YS6EN2CT7": {
                        "unit": "Hrs",
                        "description": "MemoryDB instance-hour",
                        "pricePerUnit": {"USD": "0.1580000000"},
                    }
                }
            }
        },
    },
})


def _run_script(aws_stdout_json: str) -> dict:
    """Run the real fetch_memorydb_price.sh with a fake `aws` on PATH
    that prints the given JSON for `aws pricing get-products` and exits 0.
    """
    query = json.dumps({
        "node_type": "db.r6g.large",
        "engine_type": "redis",
        "aws_region": "us-east-1",
    })
    with tempfile.TemporaryDirectory() as bin_dir_str:
        bin_dir = Path(bin_dir_str)
        aws_stub = bin_dir / "aws"
        aws_stub.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                if [[ "$1" == "pricing" && "$2" == "get-products" ]]; then
                    cat <<'AWS_JSON'
                {aws_stdout_json}
                AWS_JSON
                    exit 0
                fi
                exit 2
                """
            ),
            encoding="utf-8",
        )
        aws_stub.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        completed = subprocess.run(
            ["bash", str(SCRIPT)],
            input=query,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(completed.stdout)


def _engine_sku(engine: str, usd: str) -> dict:
    """One node-hour SKU in the shape the MemoryDB Price List returns: the
    Redis OSS and Valkey SKUs of a node type differ only in the `engine`
    attribute, the usage type suffix and the rate."""
    product = json.loads(_RESERVED_BEFORE_ONDEMAND_PRODUCT)
    product["product"]["attributes"]["engine"] = engine
    product["terms"]["OnDemand"]["ABC123.JRTCKXETXF"]["priceDimensions"][
        "ABC123.JRTCKXETXF.6YS6EN2CT7"
    ]["pricePerUnit"]["USD"] = usd
    return product


def _run_script_against_price_list(products: list[dict], engine_type: str) -> dict:
    """Run the real script with a fake `aws` that honours the engine
    TERM_MATCH filter the way the Price List API does -- so the result shows
    which SKU a run of this engine actually gets priced from."""
    query = json.dumps({
        "node_type": "db.r6g.large",
        "engine_type": engine_type,
        "aws_region": "us-east-1",
    })
    with tempfile.TemporaryDirectory() as tmp_str:
        tmp = Path(tmp_str)
        price_list = tmp / "price_list.json"
        price_list.write_text(
            json.dumps({"FormatVersion": "aws_v1", "PriceList": [json.dumps(p) for p in products]}),
            encoding="utf-8",
        )
        aws_stub = tmp / "aws"
        aws_stub.write_text(
            textwrap.dedent(
                f"""\
                #!/usr/bin/env bash
                [[ "$1" == "pricing" && "$2" == "get-products" ]] || exit 2
                engine=""
                for arg in "$@"; do
                    case "$arg" in
                        Type=TERM_MATCH,Field=engine,Value=*) engine="${{arg##*Value=}}" ;;
                    esac
                done
                jq --arg engine "$engine" \\
                    '.PriceList |= map(select($engine == "" or (fromjson | .product.attributes.engine) == $engine))' \\
                    "{price_list}"
                """
            ),
            encoding="utf-8",
        )
        aws_stub.chmod(0o755)

        env = os.environ.copy()
        env["PATH"] = f"{tmp}{os.pathsep}{env['PATH']}"
        completed = subprocess.run(
            ["bash", str(SCRIPT)],
            input=query,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )
        return json.loads(completed.stdout)


class EnginePricingTests(unittest.TestCase):
    """MemoryDB sells each node type as a Redis OSS SKU and a Valkey SKU at
    different rates (db.r6g.large, us-east-1: $0.309/h vs $0.2163/h, Price
    List fetched 2026-09-29). Each run must be priced from its own engine's
    SKU -- not the other one, and not "unavailable" for matching both."""

    SKUS = [_engine_sku("Redis", "0.3090000000"), _engine_sku("Valkey", "0.2163000000")]

    def test_valkey_run_is_priced_from_the_valkey_sku(self):
        result = _run_script_against_price_list(self.SKUS, "valkey")

        self.assertEqual(result["hourly_usd"], "0.2163000000", result)
        self.assertEqual(result["engine"], "valkey")

    def test_redis_run_is_priced_from_the_redis_sku(self):
        result = _run_script_against_price_list(self.SKUS, "redis")

        self.assertEqual(result["hourly_usd"], "0.3090000000", result)
        self.assertEqual(result["engine"], "redis")


class FetchMemoryDbPriceTests(unittest.TestCase):
    def test_reserved_term_before_ondemand_does_not_win_on_document_order(self):
        """Regression: an unscoped recursive USD walk picks whichever term
        (Reserved or OnDemand) happens to appear first in AWS's response,
        not necessarily OnDemand. Reserved's upfront-fee dimension is
        routinely $0, so this used to silently report a genuine "$0"
        hourly rate instead of falling back to unavailable.
        """
        result_json = json.dumps({
            "FormatVersion": "aws_v1",
            "PriceList": [_RESERVED_BEFORE_ONDEMAND_PRODUCT],
        })

        result = _run_script(result_json)

        self.assertEqual(result["hourly_usd"], "0.1580000000")
        self.assertEqual(result["source"], "aws_pricing_api")

    def test_missing_jq_still_produces_parseable_json(self):
        """Regression: _unavailable() builds its fallback JSON WITH jq, so
        the original code called _unavailable("missing dependency: jq") to
        report jq's own absence -- which tried to run jq, produced empty
        stdout, and exited 0. Terraform's `external` data source treats
        that as a hard failure (unparsable output), voiding the script's
        one guarantee in exactly the case it exists to guard. The jq-missing
        report must not itself depend on jq.
        """
        bash_path = shutil.which("bash")
        self.assertIsNotNone(bash_path, "bash must be resolvable to test this")

        query = json.dumps({
            "node_type": "db.r6g.large",
            "engine_type": "redis",
            "aws_region": "us-east-1",
        })
        env = {"PATH": "/nonexistent-empty-dir-for-test"}
        completed = subprocess.run(
            [bash_path, str(SCRIPT)],
            input=query,
            env=env,
            text=True,
            capture_output=True,
            check=True,
        )

        result = json.loads(completed.stdout)
        self.assertEqual(result["hourly_usd"], "")
        self.assertEqual(result["source"], "unavailable")
        self.assertIn("jq", result["reason"])

    def test_reserved_only_product_is_unavailable_not_zero(self):
        """No OnDemand term at all (e.g. a reservation-only SKU) must report
        unavailable, never fall through to a Reserved price.
        """
        reserved_only = json.loads(_RESERVED_BEFORE_ONDEMAND_PRODUCT)
        del reserved_only["terms"]["OnDemand"]
        result_json = json.dumps({
            "FormatVersion": "aws_v1",
            "PriceList": [json.dumps(reserved_only)],
        })

        result = _run_script(result_json)

        self.assertEqual(result["hourly_usd"], "")
        self.assertEqual(result["source"], "unavailable")

    def test_genuinely_zero_ondemand_price_is_unavailable_not_zero(self):
        """A real MemoryDB on-demand rate is never $0. Even if OnDemand
        itself carried a 0 (malformed data, unexpected AWS schema change),
        the script must not report it as a real price -- downstream treats
        hourly_usd == "" as unavailable and a numeric 0 as a genuine rate.
        """
        zero_ondemand = json.loads(_RESERVED_BEFORE_ONDEMAND_PRODUCT)
        zero_ondemand["terms"]["OnDemand"]["ABC123.JRTCKXETXF"]["priceDimensions"][
            "ABC123.JRTCKXETXF.6YS6EN2CT7"
        ]["pricePerUnit"]["USD"] = "0.0000000000"
        result_json = json.dumps({
            "FormatVersion": "aws_v1",
            "PriceList": [json.dumps(zero_ondemand)],
        })

        result = _run_script(result_json)

        self.assertEqual(result["hourly_usd"], "")
        self.assertEqual(result["source"], "unavailable")

    def test_two_skus_with_different_rates_are_unavailable_not_first(self):
        """The query has no engine filter. If the Price List carries a Valkey
        and a Redis OSS SKU for the same node type at different rates, taking
        the first match would silently price the run at whichever SKU AWS
        listed first. Ambiguity must surface as unavailable, naming the rates.
        """
        other_engine = json.loads(_RESERVED_BEFORE_ONDEMAND_PRODUCT)
        other_engine["product"]["sku"] = "DEF456"
        other_engine["terms"]["OnDemand"]["ABC123.JRTCKXETXF"]["priceDimensions"][
            "ABC123.JRTCKXETXF.6YS6EN2CT7"
        ]["pricePerUnit"]["USD"] = "0.2260000000"
        result_json = json.dumps({
            "FormatVersion": "aws_v1",
            "PriceList": [_RESERVED_BEFORE_ONDEMAND_PRODUCT, json.dumps(other_engine)],
        })

        result = _run_script(result_json)

        self.assertEqual(result["hourly_usd"], "")
        self.assertEqual(result["source"], "unavailable")
        self.assertIn("ambiguous", result["reason"])
        self.assertIn("0.1580000000", result["reason"])
        self.assertIn("0.2260000000", result["reason"])

    def test_duplicate_skus_with_the_same_rate_still_price_the_run(self):
        result_json = json.dumps({
            "FormatVersion": "aws_v1",
            "PriceList": [_RESERVED_BEFORE_ONDEMAND_PRODUCT, _RESERVED_BEFORE_ONDEMAND_PRODUCT],
        })

        result = _run_script(result_json)

        self.assertEqual(result["hourly_usd"], "0.1580000000")
        self.assertEqual(result["source"], "aws_pricing_api")


if __name__ == "__main__":
    unittest.main()
