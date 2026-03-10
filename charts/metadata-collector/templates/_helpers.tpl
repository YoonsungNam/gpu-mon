{{- define "metadata-collector.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "metadata-collector.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "metadata-collector.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{ include "metadata-collector.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "metadata-collector.selectorLabels" -}}
app.kubernetes.io/name: {{ include "metadata-collector.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "metadata-collector.config" -}}
collector:
  log_level: {{ .Values.config.logLevel }}
  health_port: {{ .Values.config.healthPort }}

clickhouse:
  endpoints:
    - {{ .Values.config.clickhouse.host }}:{{ .Values.config.clickhouse.port }}
  database: {{ .Values.config.clickhouse.database }}
  username: {{ .Values.config.clickhouse.username }}
  batch_size: {{ .Values.config.clickhouse.batchSize }}
  flush_interval: {{ .Values.config.clickhouse.flushInterval }}

sources:
  s2:
    enabled: {{ .Values.config.sources.s2.enabled }}
  vmware:
    enabled: {{ .Values.config.sources.vmware.enabled }}
{{- end }}
