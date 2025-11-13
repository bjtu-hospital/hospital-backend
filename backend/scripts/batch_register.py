"""
批量调用后端注册 API 的脚本（用于创建 user + patient）。

用法（示例）：
    # 启动后端服务（确保可访问）
    # 在 backend/ 目录下运行
    python scripts/batch_register.py --count 10 --base-url http://127.0.0.1:8000

脚本行为：
- 生成指定数量的患者用户（phonenumber、password、name、gender、patient_type）
- 对每个用户调用 POST /auth/register（或项目中实际的注册接口路径；请根据实际路由调整 `register_path`）
- 将 API 返回的 user/patient id 列表保存为 `generated_users.json`

注意：
- 后端必须在本地运行并能访问 /auth/register。若你的注册接口路径或参数不同，请修改 `register_path`。
- 密码明文仅用于测试，实际环境请勿使用弱密码或在生产环境运行此脚本。

"""
import argparse
import random
import string
import json
import time
import requests


def random_name(i: int):
    return f"测试用户{i:03d}"


def random_phone(i: int):
    # 简单手机号/标识，使用序号作为唯一值（根据后端要求可以调整）
    return str(i)


def random_gender():
    return random.choice(["男", "女"])


def random_patient_type():
    return random.choice(["学生", "教师", "职工", "外部"])  # 根据你的枚举值调整


def make_payload(i: int):
    phone = random_phone(i)
    name = random_name(i)
    # 按需使用固定测试密码
    pw = "123456"
    payload = {
        # 根据你后端注册 API 的字段调整下面的参数名
        "email": f"{phone}@example.com",
        "phonenumber": phone,
        "password": pw,
        "name": name,
        "gender": random_gender(),
        "patient_type": random_patient_type(),
        # 可选：student_id / identifier
        # "student_id": f"S{i:04d}",
    }
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10, help="要创建的患者数量")
    parser.add_argument("--base-url", type=str, default="http://127.0.0.1:8000", help="后端基础 URL")
    parser.add_argument("--out", type=str, default="generated_users.json", help="保存 API 返回的 user/patient 信息的文件")
    args = parser.parse_args()

    register_path = "/auth/register"  # 如果你的项目注册路径不同，请修改
    url = args.base_url.rstrip("/") + register_path

    results = []
    for i in range(1, args.count + 1):
        payload = make_payload(i)
        try:
            # 当前后端 register 接口以 query 参数接收（函数参数非 Body），
            # 使用 params 将数据放到查询字符串以兼容现有实现。
            r = requests.post(url, params=payload, timeout=10)
            if r.status_code == 200 or r.status_code == 201:
                try:
                    data = r.json()
                except Exception:
                    print(f"[{i}] 非 JSON 返回：", r.text)
                    data = {"raw": r.text}
                print(f"[{i}] 创建成功: {payload['phonenumber']} -> {data}")
                results.append({"phonenumber": payload['phonenumber'], "payload": payload, "response": data})
            else:
                print(f"[{i}] 请求失败 ({r.status_code}): {r.text}")
                results.append({"phonenumber": payload['phonenumber'], "payload": payload, "response": {"status_code": r.status_code, "text": r.text}})
        except Exception as e:
            print(f"[{i}] 请求异常: {e}")
            results.append({"phonenumber": payload['phonenumber'], "payload": payload, "response": {"error": str(e)}})
        time.sleep(0.2)

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"已保存结果到 {args.out}")


if __name__ == "__main__":
    main()
