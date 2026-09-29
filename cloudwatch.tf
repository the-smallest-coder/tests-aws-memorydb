resource "aws_cloudwatch_dashboard" "memorydb" {
  count = var.enable_cloudwatch_dashboard ? 1 : 0

  dashboard_name = "${local.cluster_id}-dashboard"

  dashboard_body = jsonencode({
    widgets = [
      # Header with cluster info
      {
        type   = "text"
        width  = 24
        height = 2
        properties = {
          markdown = "## MemoryDB Performance Dashboard\nEngine: ${var.engine_type} ${var.engine_version} | Instance: ${var.node_type}\nShards: ${var.num_shards} | Replicas per shard: ${var.num_replicas_per_shard} | Nodes billed: ${local.memorydb_node_count}"
        }
      },

      # Load Generator Section Header
      {
        type   = "text"
        width  = 24
        height = 2
        properties = {
          markdown = "## Load Generator (ECS Tasks)\nEndpoint: ${local.memorydb_endpoint}:${var.port}\nTasks: ${var.loadgen_task_count} | Threads: ${var.loadgen_memtier_threads} | Clients: ${var.loadgen_memtier_clients} | Duration: ${local.memtier_duration_label}"
        }
      },

      # Load Generator Service-Level CPU
      {
        type   = "metric"
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "CPUUtilization", "ClusterName", "${local.cluster_id}-loadgen", "ServiceName", "${local.cluster_id}-loadgen", { stat = "Average", label = "Service Average" }],
            ["...", { stat = "Minimum", label = "Service Minimum" }],
            ["...", { stat = "Maximum", label = "Service Maximum" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Load Gen Service CPU %"
          period  = 60
          yAxis = {
            left = {
              label = "Percent"
              min   = 0
              max   = 100
            }
          }
        }
      },

      # Load Generator Service-Level Memory
      {
        type   = "metric"
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/ECS", "MemoryUtilization", "ClusterName", "${local.cluster_id}-loadgen", "ServiceName", "${local.cluster_id}-loadgen", { stat = "Average", label = "Service Average" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Load Gen Service Memory %"
          period  = 60
          yAxis = {
            left = {
              label = "Percent"
              min   = 0
              max   = 100
            }
          }
        }
      },

      # Running Tasks Count
      {
        type   = "metric"
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["ECS/ContainerInsights", "RunningTaskCount", "ClusterName", "${local.cluster_id}-loadgen", { stat = "Average" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Running Tasks"
          period  = 60
          yAxis = {
            left = {
              label = "Count"
              min   = 0
            }
          }
        }
      },

      # Task-Level CPU Utilized (Container Insights)
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["ECS/ContainerInsights", "CpuUtilized", "ClusterName", "${local.cluster_id}-loadgen", { stat = "Average", label = "Avg CPU (millicores)" }],
            ["...", { stat = "Maximum", label = "Max CPU (millicores)" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Task-Level CPU Utilized"
          period  = 60
          yAxis = {
            left = {
              label = "Millicores"
            }
          }
        }
      },

      # Task-Level Memory Utilized (Container Insights)
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["ECS/ContainerInsights", "MemoryUtilized", "ClusterName", "${local.cluster_id}-loadgen", { stat = "Average", label = "Avg Memory (MB)" }],
            ["...", { stat = "Maximum", label = "Max Memory (MB)" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Task-Level Memory Utilized"
          period  = 60
          yAxis = {
            left = {
              label = "MB"
            }
          }
        }
      },

      # Task-Level Network Tx (Container Insights)
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["ECS/ContainerInsights", "NetworkTxBytes", "ClusterName", "${local.cluster_id}-loadgen", { stat = "Sum", id = "m1", visible = false }],
            [{ expression = "m1/PERIOD(m1)/1024/1024", label = "Tx Rate (MB/s)", id = "e1" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Task Network Tx Rate"
          period  = 60
          yAxis = {
            left = {
              label = "MB/s"
            }
          }
        }
      },

      # Task-Level Network Rx (Container Insights)
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["ECS/ContainerInsights", "NetworkRxBytes", "ClusterName", "${local.cluster_id}-loadgen", { stat = "Sum", id = "m1", visible = false }],
            [{ expression = "m1/PERIOD(m1)/1024/1024", label = "Rx Rate (MB/s)", id = "e1" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Task Network Rx Rate"
          period  = 60
          yAxis = {
            left = {
              label = "MB/s"
            }
          }
        }
      },

      # MemoryDB Network Performance Section Header
      {
        type   = "text"
        width  = 24
        height = 1
        properties = {
          markdown = "## MemoryDB Network Performance"
        }
      },

      # Network Bytes In (Ingress) with rate calculation
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "NetworkBytesIn", { stat = "Sum", id = "m1" }],
            [{ expression = "m1/PERIOD(m1)/1024/1024", label = "Ingress (MB/s)", id = "e1" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Network Ingress Traffic"
          period  = 300
          yAxis = {
            left = {
              label = "MB/s"
            }
          }
        }
      },

      # Network Bytes Out (Egress) with rate calculation
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "NetworkBytesOut", { stat = "Sum", id = "m1" }],
            [{ expression = "m1/PERIOD(m1)/1024/1024", label = "Egress (MB/s)", id = "e1" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Network Egress Traffic"
          period  = 300
          yAxis = {
            left = {
              label = "MB/s"
            }
          }
        }
      },

      # Combined Network Throughput in Gbps
      {
        type   = "metric"
        width  = 24
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "NetworkBytesIn", { stat = "Sum", id = "m1", visible = false }],
            [".", "NetworkBytesOut", { stat = "Sum", id = "m2", visible = false }],
            [{ expression = "m1/PERIOD(m1)*8/1000/1000/1000", label = "Ingress (Gbps)", id = "e1" }],
            [{ expression = "m2/PERIOD(m2)*8/1000/1000/1000", label = "Egress (Gbps)", id = "e2" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Network Throughput (Gbps) vs AWS Limits"
          period  = 300
          yAxis = {
            left = {
              label = "Gbps"
              min   = 0
            }
          }
        }
      },

      # Performance Metrics Section Header
      {
        type   = "text"
        width  = 24
        height = 1
        properties = {
          markdown = "## MemoryDB Performance Metrics"
        }
      },

      # CPU Utilization
      {
        type   = "metric"
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "CPUUtilization", { stat = "Average" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "CPU Utilization"
          period  = 300
          yAxis = {
            left = {
              label = "Percent"
              min   = 0
              max   = 100
            }
          }
        }
      },

      # Database Memory Usage Percentage
      {
        type   = "metric"
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "DatabaseMemoryUsagePercentage", { stat = "Average" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Memory Usage"
          period  = 300
          yAxis = {
            left = {
              label = "Percent"
              min   = 0
              max   = 100
            }
          }
        }
      },

      # Swap Usage
      {
        type   = "metric"
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "SwapUsage", { stat = "Average" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Swap Usage"
          period  = 300
          yAxis = {
            left = {
              label = "Bytes"
            }
          }
        }
      },

      # Keyspace Hit Rate
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "KeyspaceHits", { stat = "Sum", id = "hits", visible = false }],
            [".", "KeyspaceMisses", { stat = "Sum", id = "misses", visible = false }],
            [{ expression = "100*(hits/(hits+misses))", label = "Hit Rate %", id = "hitrate" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Keyspace Hit Rate"
          period  = 300
          yAxis = {
            left = {
              label = "Percent"
              min   = 0
              max   = 100
            }
          }
        }
      },

      # Keyspace Operations (Hits vs Misses)
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "KeyspaceHits", { stat = "Sum" }],
            [".", "KeyspaceMisses", { stat = "Sum" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Keyspace Hits vs Misses"
          period  = 300
        }
      },

      # Operations & Connections Section Header
      {
        type   = "text"
        width  = 24
        height = 1
        properties = {
          markdown = "## MemoryDB Operations and Connections"
        }
      },

      # Current Connections
      {
        type   = "metric"
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "CurrConnections", { stat = "Average" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Current Connections"
          period  = 300
        }
      },

      # New Connections
      {
        type   = "metric"
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "NewConnections", { stat = "Sum" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "New Connections"
          period  = 300
        }
      },

      # Evictions
      {
        type   = "metric"
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "Evictions", { stat = "Sum" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Evictions"
          period  = 300
        }
      },

      # Commands Processed (GET/SET operations)
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "GetTypeCmds", { stat = "Sum" }],
            [".", "SetTypeCmds", { stat = "Sum" }],
            [".", "HashBasedCmds", { stat = "Sum" }],
            [".", "ListBasedCmds", { stat = "Sum" }],
            [".", "SetBasedCmds", { stat = "Sum" }],
            [".", "SortedSetBasedCmds", { stat = "Sum" }]
          ]
          view    = "timeSeries"
          stacked = true
          region  = var.aws_region
          title   = "Commands Processed by Type"
          period  = 300
        }
      },

      # Total Commands/sec
      {
        type   = "metric"
        width  = 12
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "GetTypeCmds", { stat = "Sum", id = "m1", visible = false }],
            [".", "SetTypeCmds", { stat = "Sum", id = "m2", visible = false }],
            [".", "HashBasedCmds", { stat = "Sum", id = "m3", visible = false }],
            [".", "ListBasedCmds", { stat = "Sum", id = "m4", visible = false }],
            [".", "SetBasedCmds", { stat = "Sum", id = "m5", visible = false }],
            [".", "SortedSetBasedCmds", { stat = "Sum", id = "m6", visible = false }],
            [{ expression = "(m1+m2+m3+m4+m5+m6)/PERIOD(m1)", label = "Operations/sec", id = "e1" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Total Operations per Second"
          period  = 300
          yAxis = {
            left = {
              label = "ops/sec"
            }
          }
        }
      },

      # Additional Metrics Section Header
      {
        type   = "text"
        width  = 24
        height = 1
        properties = {
          markdown = "## MemoryDB Additional Metrics"
        }
      },

      # Current Items
      {
        type   = "metric"
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "CurrItems", { stat = "Average" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Current Items (Keys)"
          period  = 300
        }
      },

      # Memory Fragmentation Ratio
      {
        type   = "metric"
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "MemoryFragmentationRatio", { stat = "Average" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Memory Fragmentation"
          period  = 300
        }
      },

      # Replication Lag (if applicable)
      {
        type   = "metric"
        width  = 8
        height = 6
        properties = {
          metrics = [
            ["AWS/MemoryDB", "ReplicationLag", { stat = "Average" }]
          ]
          view    = "timeSeries"
          stacked = false
          region  = var.aws_region
          title   = "Replication Lag"
          period  = 300
          yAxis = {
            left = {
              label = "Seconds"
            }
          }
        }
      }
    ]
  })
}
