from backend.models_manifest import enabled_models, load_models


def test_hunyuan_only_when_flag_is_on(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text("models:\n  whisper-th: {repo: a/b, revision: x}\n"
                    "  hunyuan-mt-7b: {repo: c/d, enabled_by: ENABLE_HUNYUAN}\n")
    models = load_models(path)
    assert list(enabled_models(models, {})) == ["whisper-th"]
    assert list(enabled_models(models, {"ENABLE_HUNYUAN": "1"})) == ["whisper-th", "hunyuan-mt-7b"]
