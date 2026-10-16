import argparse
import os
from glob import glob
import torch

# Lazy import of estimation modules inside main() to allow --opt ref without detectron2


def get_parser():
    parser = argparse.ArgumentParser(description='Estimate Water Level')
    parser.add_argument('--test-name', type=str, required=True,
                        help='Name of the test video')
    parser.add_argument('--test-path', type=str, required=True,
                        help='Input image directory.')
    parser.add_argument('--out-dir', default='output/waterlevel',
                        help='A file or directory to save output results.')
    parser.add_argument('--opt', type=str,
                        help='Estimation options. "people", "stopsign", or "ref"')
    parser.add_argument('--reset-ref', action='store_true', default=False,
                        help='Force re-selecting homography points and reference bounding box')

    return parser.parse_args()


def main(args):

    args.water_mask_dir = os.path.join('./output/segs/', args.test_name, 'mask')

    # Support both single image file and directory
    if os.path.isfile(args.test_path):
        img_list = [args.test_path]
    elif os.path.isdir(args.test_path):
        img_list = sorted(glob(os.path.join(args.test_path, '*.jpg')) + glob(os.path.join(args.test_path, '*.png')))
    else:
        print(f"\n[LỖI] Đường dẫn không tồn tại: {args.test_path}\n")
        return

    if len(img_list) == 0:
        print(f"\n[LỖI] Không tìm thấy ảnh nào tại: {args.test_path}\n")
        return

    # Check water mask
    if os.path.isfile(args.test_path):
        base_name = os.path.splitext(os.path.basename(args.test_path))[0]
        mask_file = os.path.join(args.water_mask_dir, f'{base_name}.png')
        water_mask_list = [mask_file] if os.path.exists(mask_file) else []
    else:
        water_mask_list = sorted(glob(os.path.join(args.water_mask_dir, '*.png')))

    if len(water_mask_list) == 0:
        print(f"\n[LỖI] Không tìm thấy mặt nạ phân đoạn nước tại: {args.water_mask_dir}")
        print("LƯU Ý QUAN TRỌNG:")
        print("  1. Tham số '--test-name' phải GIỐNG HỆT tên bạn đã đặt ở bước chạy phân đoạn (test_image_seg.py / test_video_seg.py).")
        print(f"     Ví dụ: Nếu trước đó bạn chạy '--test-name results', thì ở đây cũng phải là '--test-name results'.")
        print(f"  2. Đảm bảo rằng file mask đã được sinh ra trước khi chạy ước lượng.\n")
        return

    out_dir = os.path.join(args.out_dir, f'{args.test_name}_{args.opt}')
    os.makedirs(out_dir, exist_ok=True)

    if getattr(args, 'reset_ref', False):
        for fname in ['homo_mat.txt', 'ref_bbox.txt']:
            fpath = os.path.join(out_dir, fname)
            if os.path.exists(fpath):
                os.remove(fpath)
                print(f"[Reset] Đã xóa {fname} để bạn chọn lại mốc tham chiếu mới.")

    record_dir = './records/groundtruth'

    if args.opt in ['people', 'stopsign']:
        try:
            from estimation.object_detection import est_by_obj_detection
        except ModuleNotFoundError as e:
            if 'detectron2' in str(e):
                print("\n[LỖI] Thiếu thư viện 'detectron2'!")
                print("Detectron2 là thư viện do Meta phát triển cho Linux, dùng để nhận diện biển Stop và người.")
                print("1. Nếu muốn đo mực nước bằng vật tham chiếu cố định (cột mốc / tòa nhà), hãy dùng: --opt ref")
                print("2. Nếu muốn dùng --opt stopsign hoặc --opt people, bạn cần cài Detectron2 (hoặc chạy trên môi trường Linux / WSL2).\n")
                return
            raise e
        est_by_obj_detection(img_list, water_mask_list, out_dir, args.opt)
    elif args.opt == 'ref':
        from estimation.reference_tracking import est_by_reference
        est_by_reference(img_list, water_mask_list, out_dir, record_dir, args.test_name)
    else:
        raise NotImplementedError(args.opt)

if __name__ == '__main__':

    _args = get_parser()
    print(_args)

    main(_args)
