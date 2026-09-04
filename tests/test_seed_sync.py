"""Decision table for the code-maintained seed ConfigMap sync (kube.py)."""
import hashlib

from switchyard_config import kube

ANN = kube.SEED_CHECKSUM_ANNOTATION


def runtime(annotations=None, routes=True, meta=None):
    """Runtime ConfigMap in the to_dict() shape plan_seed_sync expects."""
    data = {}
    if routes:
        data["routes.toml"] = "# runtime"
    if meta:
        data["provider_meta.json"] = meta
    return {"data": data, "metadata": {"annotations": dict(annotations or {})}}


def checksum_of(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_no_seed_is_noop():
    assert kube.plan_seed_sync(None, runtime())["action"] == "none"
    assert kube.plan_seed_sync({}, runtime())["action"] == "none"
    assert kube.plan_seed_sync({"other": "x"}, runtime())["action"] == "none"


def test_missing_runtime_is_created_from_seed():
    plan = kube.plan_seed_sync({"routes.toml": "# seed"}, None)
    assert plan["action"] == "create"
    assert plan["data"] == {"routes.toml": "# seed"}
    assert plan["checksum"] == checksum_of("# seed")


def test_empty_runtime_shell_gets_seeded():
    plan = kube.plan_seed_sync({"routes.toml": "# seed"}, runtime(routes=False))
    assert plan["action"] == "sync"
    assert plan["data"] == {"routes.toml": "# seed"}


def test_legacy_runtime_is_adopted_not_clobbered():
    # Content differs from the seed but no checksum recorded (predates the
    # feature): the existing config wins, only the checksum is stamped.
    plan = kube.plan_seed_sync({"routes.toml": "# seed"}, runtime())
    assert plan == {"action": "adopt", "checksum": checksum_of("# seed")}


def test_matching_checksum_is_noop():
    plan = kube.plan_seed_sync(
        {"routes.toml": "# seed"}, runtime({ANN: checksum_of("# seed")})
    )
    assert plan == {"action": "none"}


def test_changed_seed_clobbers():
    plan = kube.plan_seed_sync(
        {"routes.toml": "# new"}, runtime({ANN: checksum_of("# old")})
    )
    assert plan["action"] == "sync"
    assert plan["data"]["routes.toml"] == "# new"
    # Stale display-name metadata belonging to the replaced content is removed.
    assert plan["data"]["provider_meta.json"] is None


def test_changed_seed_carries_meta():
    meta = '{"vLLM": {"display_name": "vLLM"}}'
    plan = kube.plan_seed_sync(
        {"routes.toml": "# new", "provider_meta.json": meta},
        runtime({ANN: checksum_of("# old")}),
    )
    assert plan["action"] == "sync"
    assert plan["data"]["provider_meta.json"] == meta


def test_recreate_labels_drop_version_coupled_ones():
    # The create path must not claim labels Helm owns: copying
    # helm.sh/chart / app.kubernetes.io/version would make the next chart
    # upgrade conflict with the configurator's field manager.
    labels = {
        "helm.sh/chart": "switchyard-0.2.2",
        "app.kubernetes.io/version": "v0.2.2",
        "app.kubernetes.io/name": "switchyard",
        "app.kubernetes.io/managed-by": "Helm",
    }
    stable = kube._stable_labels(labels)
    assert stable == {
        "app.kubernetes.io/name": "switchyard",
        "app.kubernetes.io/managed-by": "Helm",
    }
    assert kube._stable_labels(None) == {}