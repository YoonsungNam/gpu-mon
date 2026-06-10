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
    {{- if .Values.config.sources.s2.enabled }}
    bmi_url: {{ .Values.config.sources.s2.bmiUrl | quote }}
    http_proxy_addr: {{ .Values.config.sources.s2.httpProxyAddr | quote }}
    phd_bin_template: {{ .Values.config.sources.s2.phdBinTemplate | quote }}
    ssh:
      jump_host: {{ .Values.config.sources.s2.ssh.jumpHost | quote }}
      login_host: {{ .Values.config.sources.s2.ssh.loginHost | quote }}
      {{- with .Values.config.sources.s2.ssh.user }}
      user: {{ . | quote }}
      {{- end }}
      key_path: {{ .Values.config.sources.s2.ssh.keyPath | quote }}
    target_grids:
      {{- range .Values.config.sources.s2.targetGrids }}
      - {{ . | quote }}
      {{- end }}
    intervals:
      grid_discovery: {{ .Values.config.sources.s2.intervals.gridDiscovery }}
      nodes: {{ .Values.config.sources.s2.intervals.nodes }}
      jobs_active: {{ .Values.config.sources.s2.intervals.jobsActive }}
      jobs_completed: {{ .Values.config.sources.s2.intervals.jobsCompleted }}
    {{- end }}
  vmware:
    enabled: {{ .Values.config.sources.vmware.enabled }}
{{- end }}
