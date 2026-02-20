{{- define "mock-dcgm-exporter.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "mock-dcgm-exporter.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "mock-dcgm-exporter.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{ include "mock-dcgm-exporter.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "mock-dcgm-exporter.selectorLabels" -}}
app.kubernetes.io/name: {{ include "mock-dcgm-exporter.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
