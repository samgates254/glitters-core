"""Response contracts for operational endpoints."""

from rest_framework import serializers


class ComponentStatusSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=["ok", "error"])
    detail = serializers.CharField(required=False)
    version = serializers.CharField(required=False)


class ReadyChecksSerializer(serializers.Serializer):
    database = ComponentStatusSerializer()
    postgis = ComponentStatusSerializer()
    redis = ComponentStatusSerializer()


class HealthLiveSerializer(serializers.Serializer):
    status = serializers.CharField()
    service = serializers.CharField()
    version = serializers.CharField()
    request_id = serializers.CharField()


class HealthReadySerializer(HealthLiveSerializer):
    checks = ReadyChecksSerializer()
