from embed.agentspec import AgentSpec, ROIModel
FLAGSHIP_AGENTS: dict[str, AgentSpec] = {
    "cfo": AgentSpec(id="flagship.cfo", name="AI CFO", version="1.0.0", objective="Oversee cashflow.", summary="Autonomous CFO.", category="finance", industry=["finance"], tier="enterprise", autonomy_level=4, skills=["finance.invoice_lookup"], tools=["accounting.get_invoices"], connectors=["erp.xero"], roi_model=ROIModel(estimated_manual_mins_per_task=45.0, hourly_labor_cost_zar=450.0, estimated_monthly_volume=180, projected_monthly_savings_zar=60750.0, error_reduction_percentage=94.0, payback_months=0.5)),
}
