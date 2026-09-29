#!/usr/bin/env bash
#
# fetch_memorydb_price.sh — Live AWS Price List lookup for one MemoryDB
# node's on-demand hourly rate, in the run's own region.
#
# NODE-HOURS ONLY. MemoryDB bills a second dimension -- a per-GB charge
# for data written -- that this script does not price. A cost figure built
# from this number alone systematically understates MemoryDB under write
# load. WP8 adds the data-written term; until then, treat the hourly rate
# as one of two terms, not as the cost.
#
# Invoked by Terraform's `data "external"` block in ecs.tf. Terraform's
# external-program protocol: read a JSON object from stdin (query args),
# print exactly one flat JSON object of string->string to stdout, and
# ALWAYS exit 0 — a non-zero exit fails the entire `terraform apply`, which
# a cost-reporting side value must never do.
#
# On any failure (missing aws/jq, no pricing:GetProducts permission, no
# matching SKU, several SKUs with different rates, region not in the
# location map below) this prints
# {"hourly_usd": "", "source": "unavailable", "reason": "..."} and exits 0.
# Callers must treat an empty hourly_usd as "not available", never as 0.
#
# Requires `bash` (>= 4 -- see the version guard below), the `aws` CLI, and
# `jq` on the machine running `terraform apply`/`terraform destroy` (all
# already required elsewhere in this repo, see check_status.sh). This
# script's own exit-0 contract only covers failures *inside* it; it cannot
# help if Terraform fails to launch it at all (e.g. no `bash` on PATH). That
# risk is why var.enable_price_lookup exists as an apply/destroy-time escape
# hatch -- see its description in variables.tf.
#
# The AWS Price List Query API is only served from us-east-1 (and
# ap-south-1) regardless of which region the cluster itself runs in --
# that --region below is an AWS API constraint, not a bug.

set -uo pipefail

# jq itself must never be assumed present before this point: _unavailable
# below builds its fallback JSON WITH jq, so checking for jq's absence via
# _unavailable would call jq to report that jq is missing -- empty stdout,
# exit 0, and Terraform fails the whole `external` data source on
# unparsable output, defeating the one guarantee this script makes. This
# check is a plain printf specifically so it works with no dependencies.
if ! command -v jq &>/dev/null; then
    printf '{"hourly_usd":"","source":"unavailable","reason":"missing dependency: jq"}\n'
    exit 0
fi

_unavailable() {
    local reason="$1"
    jq -n --arg reason "$reason" '{hourly_usd: "", source: "unavailable", reason: $reason}'
    exit 0
}

command -v aws &>/dev/null || _unavailable "missing dependency: aws"

# macOS ships bash 3.2 (Apple froze it there over the GPLv3 relicense and
# never upgraded); `declare -A` doesn't exist before bash 4 and its exact
# failure mode under `set -u` is unreliable across bash builds. Checked
# with BASH_VERSINFO, not `declare -A` itself, since that array is a bash
# builtin present even on 3.2 -- checking it can't itself hit the bug it's
# guarding against.
if [[ "${BASH_VERSINFO[0]}" -lt 4 ]]; then
    _unavailable "bash ${BASH_VERSINFO[0]}.${BASH_VERSINFO[1]} found; this script requires bash >= 4 for associative arrays"
fi

QUERY_JSON="$(cat)"
NODE_TYPE="$(jq -r '.node_type' <<<"$QUERY_JSON")"
ENGINE_TYPE="$(jq -r '.engine_type' <<<"$QUERY_JSON")"
AWS_REGION_ARG="$(jq -r '.aws_region' <<<"$QUERY_JSON")"

# MemoryDB prices each node type as two SKUs, told apart by the Price List's
# `engine` attribute ("Redis" / "Valkey"; not ElastiCache's cacheEngine, which
# does not exist here). Valkey is the cheaper one -- db.r6g.large in us-east-1
# is $0.2163/h vs $0.309/h for Redis OSS -- so without this filter the query
# matches both and the ambiguity check below reports "unavailable".
case "$ENGINE_TYPE" in
    redis) ENGINE_LABEL="$ENGINE_TYPE"; PRICE_LIST_ENGINE="Redis" ;;
    valkey) ENGINE_LABEL="$ENGINE_TYPE"; PRICE_LIST_ENGINE="Valkey" ;;
    *) _unavailable "unknown engine_type: $ENGINE_TYPE" ;;
esac

# AWS Price List "location" attribute string, keyed by region code. This is
# static AWS naming data (not pricing), stable across years -- not a
# staleness risk like a hardcoded price would be. Extend as needed; verify
# an unfamiliar region with:
#   aws pricing get-attribute-values --service-code AmazonMemoryDB --attribute-name location --region us-east-1
declare -A REGION_LOCATIONS=(
    [us-east-1]="US East (N. Virginia)"
    [us-east-2]="US East (Ohio)"
    [us-west-1]="US West (N. California)"
    [us-west-2]="US West (Oregon)"
    [af-south-1]="Africa (Cape Town)"
    [ap-east-1]="Asia Pacific (Hong Kong)"
    [ap-south-1]="Asia Pacific (Mumbai)"
    [ap-south-2]="Asia Pacific (Hyderabad)"
    [ap-northeast-1]="Asia Pacific (Tokyo)"
    [ap-northeast-2]="Asia Pacific (Seoul)"
    [ap-northeast-3]="Asia Pacific (Osaka)"
    [ap-southeast-1]="Asia Pacific (Singapore)"
    [ap-southeast-2]="Asia Pacific (Sydney)"
    [ap-southeast-3]="Asia Pacific (Jakarta)"
    [ap-southeast-4]="Asia Pacific (Melbourne)"
    [ca-central-1]="Canada (Central)"
    [ca-west-1]="Canada West (Calgary)"
    [eu-central-1]="EU (Frankfurt)"
    [eu-central-2]="Europe (Zurich)"
    [eu-west-1]="EU (Ireland)"
    [eu-west-2]="EU (London)"
    [eu-west-3]="EU (Paris)"
    [eu-north-1]="EU (Stockholm)"
    [eu-south-1]="EU (Milan)"
    [eu-south-2]="Europe (Spain)"
    [me-south-1]="Middle East (Bahrain)"
    [me-central-1]="Middle East (UAE)"
    [sa-east-1]="South America (Sao Paulo)"
    [il-central-1]="Israel (Tel Aviv)"
)

LOCATION="${REGION_LOCATIONS[$AWS_REGION_ARG]:-}"
[[ -n "$LOCATION" ]] || _unavailable "no known Price List location for region: $AWS_REGION_ARG"

ERR_FILE="$(mktemp)"
trap 'rm -f "$ERR_FILE"' EXIT

RESULT_JSON="$(aws pricing get-products \
    --service-code AmazonMemoryDB \
    --region us-east-1 \
    --format-version aws_v1 \
    --filters \
        "Type=TERM_MATCH,Field=instanceType,Value=${NODE_TYPE}" \
        "Type=TERM_MATCH,Field=location,Value=${LOCATION}" \
        "Type=TERM_MATCH,Field=engine,Value=${PRICE_LIST_ENGINE}" \
    --output json 2>"$ERR_FILE")"
AWS_EXIT=$?

if [[ $AWS_EXIT -ne 0 ]]; then
    REASON="aws pricing get-products failed: $(tr '\n' ' ' <"$ERR_FILE" | cut -c1-200)"
    _unavailable "$REASON"
fi

# Each PriceList entry is itself a JSON *string* that must be re-decoded.
# Every SKU carries both terms.OnDemand AND terms.Reserved -- Reserved's
# upfront-fee price dimension is routinely "0.0000000000". Scoping to
# terms.OnDemand first, THEN walking recursively with `.. | objects | .USD?`
# avoids hardcoding the OnDemand-internal SKU/rateCode nesting (which AWS
# does not guarantee is stable) without also being able to pick up
# Reserved's price by document-order accident -- `first` on an unscoped walk
# picks whichever term happens to appear first in the response, not
# necessarily OnDemand.
#
# Every distinct OnDemand rate is collected rather than the first one taken.
# instanceType + location + engine selects exactly one SKU today, but if AWS
# adds another same-type variant (a new usage type, say) the query would
# match more than one, and `first` would silently price the run at whichever
# SKU happened to be listed first. More than one distinct rate is reported
# as unavailable instead, naming the rates, so the new variant gets noticed.
PRICES_JSON="$(jq -c '
    [.PriceList[]? | fromjson | .terms.OnDemand? // {} | .. | objects | .USD? // empty] | unique
' <<<"$RESULT_JSON" 2>/dev/null)"
PRICES_JSON="${PRICES_JSON:-[]}"
PRICE_COUNT="$(jq -r 'length' <<<"$PRICES_JSON" 2>/dev/null)"

if [[ "${PRICE_COUNT:-0}" -gt 1 ]]; then
    _unavailable "ambiguous: ${PRICE_COUNT} distinct OnDemand rates for ${NODE_TYPE}/${LOCATION}/${PRICE_LIST_ENGINE} ($(jq -r 'join(", ")' <<<"$PRICES_JSON")); nothing in the query tells them apart"
fi

PRICE="$(jq -r '.[0] // empty' <<<"$PRICES_JSON" 2>/dev/null)"

[[ -n "$PRICE" && "$PRICE" != "null" ]] || \
    _unavailable "no AWS Price List OnDemand match for ${NODE_TYPE}/${LOCATION}"

# Defense in depth: a real on-demand MemoryDB rate is never $0. Reject a
# non-positive price rather than let it flow downstream as a genuine "$0"
# figure -- callers already treat an empty hourly_usd as unavailable, and a
# 0 must get the same treatment, not be mistaken for a real free tier.
IS_POSITIVE="$(jq -n --arg price "$PRICE" '(try ($price | tonumber) catch nan) > 0' 2>/dev/null)"
[[ "$IS_POSITIVE" == "true" ]] || \
    _unavailable "AWS Price List returned a non-positive OnDemand price ($PRICE) for ${NODE_TYPE}/${LOCATION}"

jq -n \
    --arg hourly_usd "$PRICE" \
    --arg location "$LOCATION" \
    --arg engine "$ENGINE_LABEL" \
    '{hourly_usd: $hourly_usd, source: "aws_pricing_api", location: $location, engine: $engine}'
