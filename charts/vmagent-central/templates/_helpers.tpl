{{/*
Expand the name of the chart.
*/}}
{{- define "vmagent-central.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "vmagent-central.fullname" -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "vmagent-central.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{ include "vmagent-central.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "vmagent-central.selectorLabels" -}}
app.kubernetes.io/name: {{ include "vmagent-central.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
