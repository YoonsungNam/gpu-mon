{{- define "clickhouse-keeper.fullname" -}}
clickhouse-keeper
{{- end }}

{{- define "clickhouse-keeper.headlessServiceName" -}}
clickhouse-keeper-headless
{{- end }}

{{- define "clickhouse-keeper.labels" -}}
app.kubernetes.io/name: clickhouse-keeper
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "clickhouse-keeper.selectorLabels" -}}
app.kubernetes.io/name: clickhouse-keeper
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
