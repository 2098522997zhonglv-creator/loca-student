# Callie 真实站点 E2E

## 运行条件

导出环境变量后执行：

```bash
export CALLIE_BASE_URL='https://...'
export CALLIE_USERNAME='...'
export CALLIE_PASSWORD='...'
export CALLIE_PRODUCT_URL='https://.../product/...'
# 可选
export CALLIE_UPLOAD_FILE='/path/to/image.png'
export CALLIE_REQUIRE_NATIVE=1
export CALLIE_ALLOW_SUBMIT_ORDER=0
export CALLIE_HEADLESS=1

cd backend
python manage.py test orchestrator_integration.callie_e2e -v2
```

未配置 `CALLIE_*` 时用例自动 skip，不进入默认全量门禁统计。

## 覆盖

1. 登录
2. 打开目标商品
3. 上传（优先 filechooser；`CALLIE_REQUIRE_NATIVE=1` 时拒绝 set_input）
4. 加购并尽量捕获 `cart_id`
5. 可选提交订单（不支付）
