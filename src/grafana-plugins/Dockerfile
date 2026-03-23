# Grafana plugin carrier image for airgap environments.
# Downloads plugins at build time (internet-connected machine),
# then serves as an init container source in K8s.
#
# Usage:
#   docker build \
#     --build-arg CLICKHOUSE_PLUGIN_VERSION=4.14.0 \
#     --build-arg VM_DATASOURCE_VERSION=0.22.0 \
#     -t grafana-plugins .
#   (version args are passed automatically by build-grafana-plugins.sh from versions.yaml)

FROM alpine:3.21 AS download

ARG CLICKHOUSE_PLUGIN_VERSION
ARG VM_DATASOURCE_VERSION
RUN test -n "$CLICKHOUSE_PLUGIN_VERSION" || { echo "ERROR: CLICKHOUSE_PLUGIN_VERSION is required"; exit 1; }
RUN test -n "$VM_DATASOURCE_VERSION" || { echo "ERROR: VM_DATASOURCE_VERSION is required"; exit 1; }

RUN apk add --no-cache curl unzip && mkdir -p /plugins

RUN curl -fsSL \
      "https://grafana.com/api/plugins/grafana-clickhouse-datasource/versions/${CLICKHOUSE_PLUGIN_VERSION}/download" \
      -o /tmp/clickhouse-plugin.zip \
    && unzip /tmp/clickhouse-plugin.zip -d /plugins/ \
    && rm /tmp/clickhouse-plugin.zip

RUN curl -fsSL \
      "https://grafana.com/api/plugins/victoriametrics-metrics-datasource/versions/${VM_DATASOURCE_VERSION}/download" \
      -o /tmp/vm-plugin.zip \
    && unzip /tmp/vm-plugin.zip -d /plugins/ \
    && rm /tmp/vm-plugin.zip

FROM busybox:1.37
COPY --from=download /plugins/ /plugins/
