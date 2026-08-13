# Push code + model to the API VM, then run deploy script ON the VM.
#
# From Windows PowerShell (inventory-ai folder), adjust USER/KEY/HOST:
#
#   $VM = "azureuser@74.249.36.238"
#   $KEY = "C:\path\to\api_vm_key.pem"
#
# 1) Push repo (if you committed):
#   git add models/global_lightgbm_sales_model.joblib models/README.md `
#     v2/forecasting/global_lightgbm_predictor.py `
#     api/services/detect_order_service.py `
#     api/repositories/detect_order_repository.py `
#     requirements.txt .env.example deploy/deploy_global_lightgbm.sh
#   git commit -m "Wire Kevin global LightGBM into detect-order forecast chart"
#   git push origin main
#
# 2) Or scp model + code without waiting on git:
#   scp -i $KEY models/global_lightgbm_sales_model.joblib ${VM}:/opt/reorder-ai/models/
#   scp -i $KEY v2/forecasting/global_lightgbm_predictor.py ${VM}:/opt/reorder-ai/v2/forecasting/
#   scp -i $KEY api/services/detect_order_service.py ${VM}:/opt/reorder-ai/api/services/
#   scp -i $KEY api/repositories/detect_order_repository.py ${VM}:/opt/reorder-ai/api/repositories/
#   scp -i $KEY deploy/deploy_global_lightgbm.sh ${VM}:/opt/reorder-ai/deploy/
#   scp -i $KEY requirements.txt ${VM}:/opt/reorder-ai/
#
# 3) On the VM:
#   cd /opt/reorder-ai && sudo bash deploy/deploy_global_lightgbm.sh
#
# Verify:
#   curl -s http://74.249.36.238:8000/api/health
#   # POST detect-order → items should show forecast_source = "global_lightgbm"
