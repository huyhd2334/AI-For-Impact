import os
import sys
import argparse
import urllib.request
import zipfile
from tqdm import tqdm

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

HF_BASE = "https://huggingface.co/xmlyqing00/V-FloodNet/resolve/main"

RECORDS_URL = f"{HF_BASE}/records.zip"
METRO_URL = f"{HF_BASE}/MeshTransformer/models/metro_release/metro_3dpw_state_dict.bin"
HRNET_URL = f"{HF_BASE}/MeshTransformer/models/hrnet/hrnetv2_w40_imagenet_pretrained.pth"


class DownloadProgressBar(tqdm):
    def update_to(self, b=1, bsize=1, tsize=None):
        if tsize is not None:
            self.total = tsize
        self.update(b * bsize - self.n)


def download_file(url, target_path):
    os.makedirs(os.path.dirname(os.path.abspath(target_path)), exist_ok=True)
    if os.path.exists(target_path):
        print(f"[Already exists] {target_path}")
        return

    print(f"[Downloading] {url} -> {target_path}")
    opener = urllib.request.build_opener()
    opener.addheaders = [('User-Agent', 'Mozilla/5.0')]
    urllib.request.install_opener(opener)
    with DownloadProgressBar(unit='B', unit_scale=True, miniters=1, desc=os.path.basename(target_path)) as t:
        urllib.request.urlretrieve(url, filename=target_path, reporthook=t.update_to)
    print(f"[Download completed] {target_path}")


def extract_zip(zip_path, extract_to):
    print(f"[Extracting] {zip_path} into {extract_to} ...")
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        zip_ref.extractall(extract_to)
    print(f"[Extracted successfully] {extract_to}")


def main():
    parser = argparse.ArgumentParser(description="Download pretrained model weights for V-FloodNet")
    parser.add_argument("--records", action="store_true", default=True,
                        help="Download records.zip (Image/Video Segmentation models and groundtruth)")
    parser.add_argument("--mesh", action="store_true", default=False,
                        help="Download MeshTransformer weights (METRO & HRNet for 3D body depth estimation)")
    parser.add_argument("--all", action="store_true", default=False,
                        help="Download all weights")
    args = parser.parse_args()

    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    os.chdir(project_root)

    if args.all:
        args.records = True
        args.mesh = True

    if args.records:
        weights_dir = os.path.join(project_root, "..", "weights")
        records_dir = os.path.join(project_root, "records")
        model_eff = os.path.join(weights_dir, "link_efficientb4_model.pth")
        model_vid = os.path.join(weights_dir, "video_seg_checkpoint_20200212-001734.pth")

        if (os.path.exists(model_eff) and os.path.exists(model_vid)) or (
            os.path.exists(os.path.join(records_dir, "link_efficientb4_model.pth")) and 
            os.path.exists(os.path.join(records_dir, "video_seg_checkpoint_20200212-001734.pth"))
        ):
            print(f"[OK] Weights already present in weights/ or records/")
        else:
            zip_dest = os.path.join(project_root, "records.zip")
            download_file(RECORDS_URL, zip_dest)
            extract_zip(zip_dest, project_root)
            if os.path.exists(zip_dest):
                try:
                    os.remove(zip_dest)
                    print(f"[Clean] Removed temporary file {zip_dest}")
                except Exception:
                    pass

    if args.mesh:
        metro_path = os.path.join(project_root, "MeshTransformer", "models", "metro_release", "metro_3dpw_state_dict.bin")
        hrnet_path = os.path.join(project_root, "MeshTransformer", "models", "hrnet", "hrnetv2_w40_imagenet_pretrained.pth")
        download_file(METRO_URL, metro_path)
        download_file(HRNET_URL, hrnet_path)
        print("\n[Notice for SMPL Body Model]")
        print("To run 3D human depth estimation (--opt people), SMPL body model is required:")
        print("  Download basicModel_neutral_lbs_10_207_0_v1.0.0.pkl from http://smplify.is.tue.mpg.de/")
        print("  Place it at: MeshTransformer/metro/modeling/data/\n")

    print("\nWeight download and check completed!")


if __name__ == "__main__":
    main()
