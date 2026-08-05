"""女友子模块：生图服务（nana-selfie）。

调用本地 ComfyUI HTTP API 生成自拍图：SDXL/Pony 系 checkpoint + IPAdapter
参考图保持形象一致（沿用 clawra 的 nana-selfie 思路，云端 fal.ai 换成本地）。

用法（独立运行验证）:
    python -m girlfriend.selfie --prompt "wearing black lingerie, bedroom"

或在主服务中通过 [[selfie 场景描述]] 命令触发（见 app/commands.py）。
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class SelfieConfig:
    comfyui_url: str = "http://127.0.0.1:8188"   # ComfyUI 服务地址
    checkpoint: str = ""                          # NSFW 模型文件名（models/checkpoints/ 下）
    reference_image: str = "girlfriend/assets/nana.png"  # 形象参考图（IPAdapter 用）
    ipadapter_weight: float = 0.7                 # 参考图影响权重（0=不参考，1=强参考）
    output_dir: str = "web/selfies"               # 生成图片保存目录（/static 挂载 web/）
    timeout: float = 300.0
    negative_prompt: str = "lowres, bad anatomy, bad hands, extra fingers, watermark, text, blurry"


# SDXL/Pony 系文生图 + IPAdapter 参考图 workflow（节点图，node id 字符串）
def build_workflow(checkpoint: str, prompt: str, ref_image: str,
                   negative: str, weight: float,
                   width: int = 768, height: int = 1024, seed: int = -1) -> dict:
    return {
        "3": {"class_type": "KSampler",
              "inputs": {"seed": seed if seed >= 0 else int(time.time() * 1000) % 2**31,
                         "steps": 28, "cfg": 6.0, "sampler_name": "euler",
                         "scheduler": "normal", "denoise": 1.0,
                         "model": ["13", 0], "positive": ["6", 0],
                         "negative": ["7", 0], "latent_image": ["8", 0]}},
        "5": {"class_type": "CheckpointLoaderSimple",
              "inputs": {"ckpt_name": checkpoint}},
        "6": {"class_type": "CLIPTextEncode",
              "inputs": {"text": prompt, "clip": ["5", 1]}},
        "7": {"class_type": "CLIPTextEncode",
              "inputs": {"text": negative, "clip": ["5", 1]}},
        "8": {"class_type": "EmptyLatentImage",
              "inputs": {"width": width, "height": height, "batch_size": 1}},
        "9": {"class_type": "VAEDecode",
              "inputs": {"samples": ["3", 0], "vae": ["5", 2]}},
        "10": {"class_type": "SaveImage",
               "inputs": {"filename_prefix": "nana_selfie", "images": ["9", 0]}},
        # IPAdapter 参考图（形象一致）
        "11": {"class_type": "IPAdapterUnifiedLoader",
               "inputs": {"model": ["5", 0], "preset": "PLUS (high strength)"}},
        "12": {"class_type": "LoadImage",
               "inputs": {"image": ref_image}},
        "13": {"class_type": "IPAdapterAdvanced",
               "inputs": {"model": ["11", 0], "ipadapter": ["11", 1],
                          "image": ["12", 0], "weight": weight,
                          "start_at": 0.0, "end_at": 1.0,
                          "weight_type": "linear"}},
    }


class SelfieService:
    """ComfyUI 客户端：提交 workflow → 轮询 → 返回图片文件路径。"""

    def __init__(self, config: SelfieConfig, base_dir: Path):
        self.config = config
        self.base_dir = base_dir
        self.client_id = str(uuid.uuid4())
        (base_dir / config.output_dir).mkdir(parents=True, exist_ok=True)

    def ref_image_path(self) -> str | None:
        """参考图（ComfyUI 内用相对路径：input 目录下）。返回绝对路径并确认存在。"""
        p = self.base_dir / self.config.reference_image
        return str(p) if p.exists() else None

    def _api(self, path: str, method: str = "GET", body: dict | None = None) -> dict:
        url = self.config.comfyui_url + path
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.config.timeout) as r:
            return json.loads(r.read().decode())

    def generate(self, prompt: str, ref_image: str | None = None,
                 width: int = 768, height: int = 1024) -> Path:
        """生成一张自拍图，返回保存的文件路径。"""
        if not self.config.checkpoint:
            raise RuntimeError(
                "未配置 NSFW 生图模型（[girlfriend] checkpoint），请下载模型后放入 "
                "ComfyUI/models/checkpoints/ 并填写文件名")
        ref = ref_image or self.ref_image_path()
        workflow = build_workflow(
            self.config.checkpoint, prompt, ref or "",
            self.config.negative_prompt, self.config.ipadapter_weight,
            width=width, height=height)
        if ref is None:
            # 无参考图时移除 IPAdapter 分支
            workflow.pop("11", None); workflow.pop("12", None); workflow.pop("13", None)
            workflow["3"]["inputs"]["model"] = ["5", 0]

        resp = self._api("/prompt", "POST", {"prompt": workflow, "client_id": self.client_id})
        prompt_id = resp.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI 提交失败: {resp.get('node_errors') or resp}")

        # 轮询 /history 直到出图
        for _ in range(int(self.config.timeout / 2)):
            time.sleep(2)
            history = self._api(f"/history/{prompt_id}")
            if prompt_id in history:
                outputs = history[prompt_id].get("outputs", {})
                for node_out in outputs.values():
                    for img in node_out.get("images", []):
                        fname = img["filename"]
                        subdir = img.get("subfolder", "")
                        out = self.base_dir / self.config.output_dir / fname
                        # 从 ComfyUI 输出目录拉取
                        self._download_image(fname, subdir, out)
                        return out
                break
        raise RuntimeError("ComfyUI 生成超时")

    def _download_image(self, filename: str, subfolder: str, dest: Path) -> None:
        url = f"{self.config.comfyui_url}/view?filename={urllib.request.quote(filename)}"
        if subfolder:
            url += f"&subfolder={urllib.request.quote(subfolder)}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=120) as r:
            dest.write_bytes(r.read())


def main() -> None:
    import argparse

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser(description="本地自拍生图（ComfyUI）")
    ap.add_argument("--prompt", required=True, help="画面描述")
    ap.add_argument("--checkpoint", default="", help="覆盖配置中的模型文件名")
    args = ap.parse_args()

    import tomllib
    cfg_path = Path(__file__).resolve().parent.parent / "config.toml"
    cfg = {}
    if cfg_path.exists():
        with open(cfg_path, "rb") as f:
            cfg = tomllib.load(f).get("girlfriend", {})
    if args.checkpoint:
        cfg["checkpoint"] = args.checkpoint
    svc = SelfieService(SelfieConfig(**cfg), Path(__file__).resolve().parent.parent)
    print("生成中…（需 ComfyUI 运行于", svc.config.comfyui_url, "）")
    out = svc.generate(args.prompt)
    print("已保存:", out)


if __name__ == "__main__":
    main()
