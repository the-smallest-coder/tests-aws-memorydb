import json
import os

import boto3


ecs = boto3.client("ecs")
memorydb = boto3.client("memorydb")


def handler(event, context):
    """
    Lambda handler for shutdown orchestration.

    This Lambda only issues shutdown commands. Verification, CloudWatch export,
    HTML report generation, and final report-ready email are handled after the
    verify_shutdown Lambda confirms cleanup.
    """

    cluster_id = os.environ["CLUSTER_ID"]
    ecs_cluster = os.environ["ECS_CLUSTER"]
    ecs_service = os.environ["ECS_SERVICE"]
    memorydb_cluster_name = os.environ["MEMORYDB_CLUSTER_NAME"]

    results = {
        "cluster_id": cluster_id,
        "ecs_stopped": False,
        "memorydb_stopped": False,
    }

    try:
        ecs.update_service(
            cluster=ecs_cluster,
            service=ecs_service,
            desiredCount=0,
        )
        results["ecs_stopped"] = True
        print(f"ECS service {ecs_service} scaled to 0")
    except Exception as exc:
        print(f"ECS stop note: {exc}")
        results["ecs_stopped"] = str(exc)

    try:
        # MemoryDB's delete_cluster has no RetainPrimaryCluster equivalent --
        # deleting the cluster deletes every shard and replica with it. The
        # snapshot parameter is FinalSnapshotName, not FinalSnapshotIdentifier.
        delete_params = {"ClusterName": memorydb_cluster_name}
        final_snapshot_name = os.environ.get("MEMORYDB_FINAL_SNAPSHOT_NAME")
        if final_snapshot_name:
            delete_params["FinalSnapshotName"] = final_snapshot_name

        memorydb.delete_cluster(**delete_params)
        results["memorydb_stopped"] = True
        print(f"MemoryDB {memorydb_cluster_name} delete initiated")
    except Exception as exc:
        print(f"MemoryDB delete note: {exc}")
        results["memorydb_stopped"] = str(exc)

    print("Shutdown commands issued. Verification will handle export/report handoff.")
    return {
        "statusCode": 200,
        "body": json.dumps(results),
    }
