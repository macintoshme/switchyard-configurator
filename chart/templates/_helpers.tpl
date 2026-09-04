{{/* Chart name; defaults to .Chart.Name, overridable via nameOverride. */}}
{{- define "switchyard.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Fully-qualified app name; collapses <release>-<chart> duplication. */}}
{{- define "switchyard.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
Selector labels shared by Deployments and Services.
Call with (dict "root" $ "component" "server"|"configurator").
*/}}
{{- define "switchyard.selectorLabels" -}}
app.kubernetes.io/name: {{ include "switchyard.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
Common labels (standard set plus selector labels).
Call with (dict "root" $ "component" "server"|"configurator").
*/}}
{{- define "switchyard.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .root.Chart.Name .root.Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
{{ include "switchyard.selectorLabels" . }}
{{- end -}}

{{- define "switchyard.configMapName" -}}
{{- printf "%s-config" (include "switchyard.fullname" .) -}}
{{- end -}}

{{/*
Name of the code-maintained seed ConfigMap the configurator follows.
*/}}
{{- define "switchyard.seedConfigMapName" -}}
{{- printf "%s-config-seed" (include "switchyard.fullname" .) -}}
{{- end -}}

{{/*
Token-secret feature gate. dig-based default keeps upgrades working when
the deployed release predates the key (--reuse-values passes the old
computed values, where configurator.tokenSecret does not exist yet).
*/}}
{{- define "switchyard.tokenSecretEnabled" -}}
{{- $ts := dig "tokenSecret" (dict "enabled" true) .Values.configurator -}}
{{- if $ts.enabled -}}true{{- end -}}
{{- end -}}

{{/*
Name of the UI-managed Secret that stores provider tokens entered in the
configurator web UI. Created/patched by the configurator itself (never
rendered by the chart, so Helm cannot conflict with it); both deployments
load it via envFrom.
*/}}
{{- define "switchyard.tokenSecretName" -}}
{{- $ts := dig "tokenSecret" (dict) .Values.configurator -}}
{{- default (printf "%s-tokens" (include "switchyard.fullname" .)) $ts.name -}}
{{- end -}}

{{/*
Namespace all chart resources are deployed into. Defaults to the dedicated
namespace from values; falls back to the helm release namespace (-n) when
namespace.name is empty.
*/}}
{{- define "switchyard.namespace" -}}
{{- default .Release.Namespace .Values.namespace.name | default "default" -}}
{{- end -}}

{{- define "switchyard.image" -}}
{{- $img := .Values.switchyard.image -}}
{{- $tag := default .Chart.AppVersion $img.tag -}}
{{- if $img.registry -}}
{{- printf "%s/%s:%s" $img.registry $img.repository $tag -}}
{{- else -}}
{{- printf "%s:%s" $img.repository $tag -}}
{{- end -}}
{{- end -}}

{{- define "configurator.fullname" -}}
{{- printf "%s-configurator" (include "switchyard.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "configurator.image" -}}
{{- $img := .Values.configurator.image -}}
{{- $tag := default .Chart.AppVersion $img.tag -}}
{{- if $img.registry -}}
{{- printf "%s/%s:%s" $img.registry $img.repository $tag -}}
{{- else -}}
{{- printf "%s:%s" $img.repository $tag -}}
{{- end -}}
{{- end -}}

{{- define "switchyard.providerEnv" -}}
{{- range .Values.providers }}
- name: {{ required "providers[].envVar is required" .envVar }}
  valueFrom:
    secretKeyRef:
      name: {{ required "providers[].secret is required" .secret }}
      key: {{ default "token" .secretKey }}
{{- end }}
{{- end }}