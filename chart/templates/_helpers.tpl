{{- define "switchyard.fullname" -}}
{{- printf "%s-%s" .Release.Name "switchyard" | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "switchyard.labels" -}}
app.kubernetes.io/name: {{ include "switchyard.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "configurator.fullname" -}}
{{- printf "%s-%s" .Release.Name "configurator" | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{- define "configurator.labels" -}}
app.kubernetes.io/name: {{ include "configurator.fullname" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}