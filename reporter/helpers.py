"""Shared helpers, constants and small utilities for the report generator."""

import pandas as pd

import metric_source

# ------------------------------------------------------------------ #
#  Design tokens / shared constants                                    #
# ------------------------------------------------------------------ #

FONT_FAMILY = "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"

LINE_OPTS = dict(width=2)

LAYOUT_BASE = dict(
    template='plotly_white',
    hovermode='x unified',
    margin=dict(l=70, r=30, t=40, b=100),
    plot_bgcolor='#ffffff',
    paper_bgcolor='#ffffff',
    font=dict(family=FONT_FAMILY, size=12),
)

LEGEND_H = dict(
    orientation='h', xanchor='center', yanchor='top', font=dict(size=11),
    bgcolor='rgba(255,255,255,0)', borderwidth=0,
)

# Colour palette
C_THROUGHPUT   = '#1f77b4'
C_LATENCY      = '#d62728'
C_OOM_BAR      = '#ef5350'
C_EVICTION_CW  = '#b71c1c'
C_HIT_RATE     = '#e67e22'
C_CPU_ECS      = '#2ca02c'
C_ENGINE_CPU   = '#ff7f0e'
C_NET_TX_ECS   = '#0097a7'
C_NET_TX_CACHE = '#00695c'
C_ECS_MEM      = '#f57c00'
MEM_COLORS     = ['#9467bd', '#ff7f0e', '#8c564b', '#e377c2']

# Deep-dive figure colours
C_CREDIT_BAL   = '#27ae60'
C_CREDIT_USE   = '#e74c3c'
# Read vs write, deliberately far apart on the colour wheel: on MemoryDB
# these two series are an order of magnitude apart and must never be mistaken
# for one another.
C_LAT_READ     = '#2196f3'
C_LAT_WRITE    = '#ff5722'
C_THROTTLE_IN  = '#f44336'
C_THROTTLE_OUT = '#b71c1c'
C_THROTTLE_PPS = '#ff9800'
C_CURR_CONN    = '#00838f'
C_MEM_FRAG     = '#795548'
C_LAT_P50      = '#1f77b4'
C_LAT_P99      = '#d62728'
C_LAT_P999     = '#7b1fa2'
C_LAT_WORST99  = '#ff7f0e'
C_LAT_WORST999 = '#111827'

CLIENT_LATENCY_NAMESPACE = metric_source.LOADGEN_NAMESPACE
CLIENT_LATENCY_METRIC = 'ClientLatency'
CLIENT_LATENCY_STATS = ('p50', 'p99', 'p99.9')

# Minimum ratio the runner-up AZ's median latency must clear over the
# lowest AZ's for `memorydb_availability_zone` to call a winner. Below
# this, the two AZs are within noise and the inference is not trustworthy.
AZ_INFERENCE_MIN_SEPARATION_RATIO = 1.5


# ------------------------------------------------------------------ #
#  I/O helper                                                          #
# ------------------------------------------------------------------ #

def read_file_content(path):
    """Read file content from a local path or an S3 URI (s3://bucket/key)."""
    if path.startswith('s3://'):
        import boto3
        parts = path[5:].split('/', 1)
        if len(parts) != 2 or not parts[1]:
            raise ValueError(f"Invalid S3 URI: {path}")
        bucket, key = parts
        obj = boto3.client('s3').get_object(Bucket=bucket, Key=key)
        return obj['Body'].read().decode('utf-8')
    else:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()


# ------------------------------------------------------------------ #
#  DataFrame helpers                                                   #
# ------------------------------------------------------------------ #

def metric_filter(df, name, stat, dim_prefix=None):
    """Return rows from *df* matching MetricName/Stat (and optional Dimensions prefix)."""
    if df.empty:
        return df
    mask = (df['MetricName'] == name) & (df['Stat'] == stat)
    if dim_prefix:
        mask = mask & df['Dimensions'].str.startswith(dim_prefix)
    return df[mask]


def client_latency_series(df):
    """Return ECS load-generator client latency percentile series from EMF CloudWatch rows."""
    columns = [
        'Timestamp',
        'p50_ms',
        'p99_ms',
        'p999_ms',
        'worst_p99_ms',
        'worst_p999_ms',
    ]
    if df.empty:
        return pd.DataFrame(columns=columns)

    required = {'Timestamp', 'Namespace', 'MetricName', 'Stat', 'Value', 'Dimensions'}
    if not required.issubset(df.columns):
        return pd.DataFrame(columns=columns)

    source = df[
        (df['Namespace'] == CLIENT_LATENCY_NAMESPACE)
        & (df['MetricName'] == CLIENT_LATENCY_METRIC)
        & (df['Stat'].isin(CLIENT_LATENCY_STATS))
    ].copy()
    if source.empty:
        return pd.DataFrame(columns=columns)

    aggregate = (
        source.groupby(['Timestamp', 'Stat'], as_index=False)['Value']
        .mean()
        .pivot(index='Timestamp', columns='Stat', values='Value')
        .reset_index()
        .rename(columns={'p50': 'p50_ms', 'p99': 'p99_ms', 'p99.9': 'p999_ms'})
    )
    worst = (
        source[source['Stat'].isin(('p99', 'p99.9'))]
        .groupby(['Timestamp', 'Stat'], as_index=False)['Value']
        .max()
        .pivot(index='Timestamp', columns='Stat', values='Value')
        .reset_index()
        .rename(columns={'p99': 'worst_p99_ms', 'p99.9': 'worst_p999_ms'})
    )
    merged = aggregate.merge(worst, on='Timestamp', how='outer').sort_values('Timestamp')
    for column in columns:
        if column not in merged.columns:
            merged[column] = pd.NA
    return merged[columns].reset_index(drop=True)


def memorydb_availability_zone(
    task_latency_map=None,
    task_az_map=None,
    measured_availability_zone=None,
):
    """Return the MemoryDB node AZ and whether it was observed or inferred.

    MemoryDB has no AZ-placement argument, so ``cluster_details.json`` records
    what AWS actually chose (``placement.shards[].nodes[].availability_zone``)
    rather than what was requested. When every node landed in one AZ, that AZ
    is passed in as *measured_availability_zone* and is authoritative. A
    placement spanning several AZs is not passed in at all (see
    ``report_generator._config_from_cluster_details``): the replicas sit in
    other AZs and nothing recorded says which node is the primary.

    Runs without a measured AZ infer the co-located AZ
    from the AZ-level median of each task's final memtier p50 latency
    (``*.totals.json``): tasks sharing an AZ with the node see lower latency.

    ``task_latency_map`` (task_id -> p50 latency ms) and ``task_az_map``
    (task_id -> AZ, from Container Insights) must both be keyed by the plain
    ECS task ID. memtier's ``stream_id`` and Container Insights' ``TaskId``
    share that one namespace, so no ID-translation table is needed.

    Note: the ECS ``ClientLatency`` EMF metric is deliberately *not* used as
    a source here. Its ``TaskId`` dimension is a UUID the container
    generates itself (``/proc/sys/kernel/random/uuid``), which shares no
    namespace with the ECS-assigned task ID used everywhere else (Container
    Insights, memtier log stream names). Joining on it always drops every
    row and silently yields ``source: 'unavailable'``.

    The lowest-median AZ only wins if it clears the runner-up by at least
    ``AZ_INFERENCE_MIN_SEPARATION_RATIO``. Two AZs within that margin of each
    other are noise, not signal - calling a winner there would be exactly as
    overconfident as the alphabetical-fallback bug this replaced, just with
    better-looking numbers behind it. That case reports ``source:
    'ambiguous'`` instead of guessing.
    """
    measured = str(measured_availability_zone or '').strip()
    if measured and measured.lower() != 'unknown':
        return {
            'availability_zone': measured,
            'source': 'cluster_details',
        }

    task_az_map = task_az_map or {}
    task_latency_map = task_latency_map or {}
    rows = [
        {'AvailabilityZone': str(task_az_map[task_id]), 'Value': latency}
        for task_id, latency in task_latency_map.items()
        if task_id in task_az_map
        and str(task_az_map[task_id]).strip().lower() != 'unknown'
        and latency is not None
        and not pd.isna(latency)
    ]
    if not rows:
        return {'availability_zone': None, 'source': 'unavailable'}

    az_medians = (
        pd.DataFrame(rows)
        .groupby('AvailabilityZone', as_index=False)['Value']
        .median()
        .sort_values(['Value', 'AvailabilityZone'], kind='stable')
    )
    if len(az_medians) >= 2:
        lowest = float(az_medians.iloc[0]['Value'])
        runner_up = float(az_medians.iloc[1]['Value'])
        # A zero (or negative) median cannot support a meaningful ratio.  Do
        # not let the stable alphabetical tie-break below turn it into a
        # confidently inferred AZ.
        if lowest <= 0 or runner_up < lowest * AZ_INFERENCE_MIN_SEPARATION_RATIO:
            return {'availability_zone': None, 'source': 'ambiguous'}
    return {
        'availability_zone': str(az_medians.iloc[0]['AvailabilityZone']),
        'source': 'inferred_from_memtier_task_p50_latency',
    }


def ecs_task_index_map(task_ids, task_az_map=None, memorydb_az=None):
    """Assign deterministic fleet-wide task indexes with the MemoryDB AZ first."""
    task_az_map = task_az_map or {}
    normalized_ids = sorted({
        str(task_id)
        for task_id in task_ids
        if task_id is not None and str(task_id)
    })
    cluster_az = str(memorydb_az or '').strip()
    known_azs = sorted({
        str(task_az_map.get(task_id))
        for task_id in normalized_ids
        if task_az_map.get(task_id)
        and str(task_az_map.get(task_id)).lower() != 'unknown'
    })
    ordered_azs = ([cluster_az] if cluster_az in known_azs else []) + [
        az for az in known_azs if az != cluster_az
    ]
    az_rank = {az: rank for rank, az in enumerate(ordered_azs)}

    def sort_key(task_id):
        az = str(task_az_map.get(task_id) or 'unknown')
        if az.lower() == 'unknown':
            return (len(az_rank) + 1, '', task_id)
        return (az_rank.get(az, len(az_rank)), az, task_id)

    return {
        task_id: index
        for index, task_id in enumerate(sorted(normalized_ids, key=sort_key), start=1)
    }


def ecs_task_metric_rows(df, metric_name, stat, value_scale=1.0, task_az_map=None):
    """Return one deduplicated task-level ECS metric row per timestamp and task."""
    columns = ['Timestamp', 'TaskId', 'AvailabilityZone', 'Value']
    if df.empty or not {'Timestamp', 'MetricName', 'Stat', 'Value', 'Dimensions'}.issubset(df.columns):
        return pd.DataFrame(columns=columns)

    source = metric_filter(df, metric_name, stat)
    if source.empty:
        return pd.DataFrame(columns=columns)

    task_rows = source[
        source['Dimensions'].astype(str).str.contains(r'(?:^|;)TaskId=', regex=True, na=False)
    ].copy()
    if task_rows.empty:
        return pd.DataFrame(columns=columns)

    dim_text = task_rows['Dimensions'].astype(str)
    task_rows['TaskId'] = dim_text.str.extract(r'(?:^|;)TaskId=([^;]+)', expand=False)
    task_rows['AvailabilityZone'] = dim_text.str.extract(
        r'(?:^|;)AvailabilityZone=([^;]+)', expand=False
    )
    if task_az_map:
        task_rows['AvailabilityZone'] = task_rows['AvailabilityZone'].fillna(
            task_rows['TaskId'].map(task_az_map)
        )
    task_rows['AvailabilityZone'] = task_rows['AvailabilityZone'].fillna('unknown')
    task_rows['Value'] = pd.to_numeric(task_rows['Value'], errors='coerce') * value_scale
    task_rows = task_rows.dropna(subset=['Timestamp', 'TaskId', 'Value'])
    if task_rows.empty:
        return pd.DataFrame(columns=columns)

    task_rows['DimPriority'] = 2
    task_rows.loc[dim_text.str.contains(r'(?:^|;)TaskDefinitionFamily=', regex=True, na=False), 'DimPriority'] = 1
    task_rows.loc[dim_text.str.contains(r'(?:^|;)ServiceName=', regex=True, na=False), 'DimPriority'] = 0
    best_priority = task_rows.groupby(['Timestamp', 'TaskId'])['DimPriority'].transform('min')
    task_rows = task_rows[task_rows['DimPriority'] == best_priority]

    return (
        task_rows.groupby(['Timestamp', 'TaskId', 'AvailabilityZone'], as_index=False)['Value']
        .mean()
        .sort_values(['Timestamp', 'TaskId'])
        .reset_index(drop=True)[columns]
    )


def ecs_task_metric_distribution(df, metric_name, stat, value_scale=1.0):
    """Aggregate one ECS metric across task-level dimensions per timestamp.

    CloudWatch exports the same Container Insights metric at several dimension
    levels. For cross-load-generator distribution charts, keep exactly one
    task-scoped source per TaskId and ignore service/cluster aggregates.
    """
    columns = ['Timestamp', 'avg', 'median', 'min', 'max', 'sum', 'source_count']
    per_task = ecs_task_metric_rows(df, metric_name, stat, value_scale=value_scale)
    if per_task.empty:
        return pd.DataFrame(columns=columns)
    grouped = per_task.groupby('Timestamp')['Value']
    result = grouped.agg(
        avg='mean',
        median='median',
        min='min',
        max='max',
        sum='sum',
        source_count='count',
    ).reset_index()
    result['source_count'] = result['source_count'].astype(int)
    return result.sort_values('Timestamp').reset_index(drop=True)[columns]


def keyspace_hit_rate_df(df):
    """Return a derived hit-rate series from KeyspaceHits/KeyspaceMisses.

    Always derived, never read directly: MemoryDB publishes no CacheHitRate
    equivalent. The source project tried a direct lookup first and fell back
    to deriving; keeping that fallback here would mean a lookup that can only
    ever miss, which reads like a supported path and is not one.
    """
    hits = metric_filter(df, metric_source.KEYSPACE_HITS, 'Sum', metric_source.CLUSTER_DIMENSION)
    misses = metric_filter(df, metric_source.KEYSPACE_MISSES, 'Sum', metric_source.CLUSTER_DIMENSION)
    empty = df.iloc[0:0]
    if hits.empty or misses.empty:
        return empty

    hit_cols = hits[['Timestamp', 'Dimensions', 'Value']].rename(columns={'Value': 'Hits'})
    miss_cols = misses[['Timestamp', 'Dimensions', 'Value']].rename(columns={'Value': 'Misses'})
    merged = hit_cols.merge(miss_cols, on=['Timestamp', 'Dimensions'], how='inner')
    total = merged['Hits'] + merged['Misses']
    merged = merged[total > 0].copy()
    if merged.empty:
        return empty

    merged['Value'] = (merged['Hits'] / (merged['Hits'] + merged['Misses'])) * 100.0
    merged['Namespace'] = metric_source.NAMESPACE
    merged['MetricName'] = 'KeyspaceHitRate'
    merged['Stat'] = 'Average'
    merged['Unit'] = 'Percent'
    return merged[['Timestamp', 'Namespace', 'MetricName', 'Stat', 'Value', 'Unit', 'Dimensions']]


def select_node_dimension_rows(df, cluster_id=None):
    """Return one set of rows per metric without double-counting.

    *df* must already be filtered to one MetricName/Stat combination (see
    ``metric_filter``). MemoryDB publishes most metrics at both the cluster
    level (``ClusterName=x``) and the node level
    (``ClusterName=x;NodeName=y``), with the cluster row being the aggregate;
    summing both counts everything twice.

    Simpler than the ElastiCache original, and deliberately so. There, the
    node dimension (``CacheClusterId``) doubled as the cluster dimension for
    non-cluster-mode groups, so a bare ``CacheClusterId=<rg>-001`` could be
    either an aggregate or one node and the code had to guess. MemoryDB keeps
    the two dimensions distinct, so the aggregate is exactly the row with no
    node dimension. There is also no ``NodeGroupId`` third axis to fold in.
    """
    if df.empty:
        return df

    dimensions = df['Dimensions'].astype(str)
    if cluster_id:
        exact_dimension = metric_source.cluster_dim(cluster_id)
        candidates = df[
            dimensions.eq(exact_dimension)
            | dimensions.str.startswith(f'{exact_dimension};')
        ]
        if candidates.empty:
            return candidates
        candidate_dims = candidates['Dimensions'].astype(str)
        aggregate = candidates[candidate_dims == exact_dimension]
        node_rows = candidates[
            candidate_dims.str.contains(f';{metric_source.NODE_DIMENSION}=')
        ]
    else:
        aggregate_mask = dimensions.str.match(metric_source.CLUSTER_ONLY_PATTERN)
        aggregate = df[aggregate_mask]
        node_rows = df[~aggregate_mask]

    return aggregate if not aggregate.empty else node_rows


def cloudwatch_eviction_series(df, cluster_id=None):
    """Return one CloudWatch Evictions series without mixing aggregate and node rows."""
    evictions = metric_filter(df, 'Evictions', 'Sum', metric_source.CLUSTER_DIMENSION)
    selected = select_node_dimension_rows(evictions, cluster_id)
    if selected.empty:
        return selected

    series = selected.groupby('Timestamp', as_index=False)['Value'].sum()
    series['Namespace'] = metric_source.NAMESPACE
    series['MetricName'] = 'Evictions'
    series['Stat'] = 'Sum'
    series['Unit'] = selected['Unit'].iloc[0] if 'Unit' in selected else 'Count'
    series['Dimensions'] = metric_source.cluster_dim(cluster_id) if cluster_id else 'selected_eviction_series'
    return series[['Timestamp', 'Namespace', 'MetricName', 'Stat', 'Value', 'Unit', 'Dimensions']]


def first_positive_timestamp(df):
    """Return the first absolute timestamp with a positive Value."""
    if df.empty:
        return None
    positive = df[df['Value'] > 0]
    return None if positive.empty else positive['Timestamp'].min()


def aggregate_memtier_progress(df):
    """Aggregate per-stream progress samples at exact CloudWatch event timestamps."""
    if df.empty:
        return pd.DataFrame()
    per_task = df.groupby(["Timestamp", "Stream"], as_index=False).agg({
        "Ops/sec": "mean",
        "Latency (ms)": "mean",
    })
    rows = []
    for timestamp, group in per_task.groupby("Timestamp"):
        ops = group["Ops/sec"]
        latency = group["Latency (ms)"]
        total_ops = float(ops.sum())
        rows.append({
            "Timestamp": timestamp,
            "Overall Ops/sec": total_ops,
            "Overall Latency (ms)": float((latency * ops).sum() / total_ops) if total_ops else 0.0,
            "Ops median": float(ops.median()),
            "Ops average": float(ops.mean()),
            "Ops p10": float(ops.quantile(0.10)),
            "Ops p90": float(ops.quantile(0.90)),
            "Ops min": float(ops.min()),
            "Ops max": float(ops.max()),
            "Latency median": float(latency.median()),
            "Latency average": float(latency.mean()),
            "Latency p10": float(latency.quantile(0.10)),
            "Latency p90": float(latency.quantile(0.90)),
            "Latency min": float(latency.min()),
            "Latency max": float(latency.max()),
        })
    return pd.DataFrame(rows).sort_values("Timestamp").reset_index(drop=True)


def shorten_dim(dim, cluster_id=''):
    """Return a concise label from a CloudWatch Dimensions string.

    Prefers the node id, else strips the cluster_id prefix from a
    ClusterName/ServiceName value so legends stay readable. MemoryDB has no
    NodeGroupId dimension, so unlike the ElastiCache original there is only
    one node-identifying key to try.
    """
    kv = dict(part.split('=', 1) for part in dim.split(';') if '=' in part)
    for key in (metric_source.NODE_DIMENSION,):
        if key in kv:
            return kv[key]
    for key in ('ServiceName', 'ClusterName'):
        if key in kv:
            val = kv[key]
            if cluster_id and val.startswith(cluster_id):
                suffix = val[len(cluster_id):].lstrip('-') or val
                return suffix if suffix else val
            return val
    first_val = next(iter(kv.values()), dim)
    return first_val


def select_mem_dims(dim_series, node_count):
    """Choose which CloudWatch dimension sets to plot for memory metrics.

    Single node (node_count == 1): the cluster aggregate only.
    More than one node: one line per node.

    MemoryDB has two dimension levels, not three. The ElastiCache original
    had a NodeGroupId (per-shard) level between the aggregate and the node,
    and preferred it; here shard identity is carried in the node name
    (``{cluster}-{shard:04d}-{node:03d}``) rather than in a dimension, so
    there is nothing to prefer and no third branch.
    """
    unique = list(dict.fromkeys(dim_series))

    def is_node_level(d):
        return f';{metric_source.NODE_DIMENSION}=' in d

    per_node = [d for d in unique if is_node_level(d)]
    aggregate = [d for d in unique if not is_node_level(d)]

    try:
        n = int(node_count)
    except (TypeError, ValueError):
        n = 0

    if n <= 1:
        # node_count can be unavailable. Retain every node series rather than
        # silently computing cards from one arbitrary node.
        return aggregate if aggregate else per_node
    return per_node if per_node else aggregate
