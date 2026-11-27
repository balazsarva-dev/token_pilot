"""Small synthetic exports with the exact headers of real provider exports (fake ids)."""

OPENAI_USAGE_HEADER = (
    "start_time,end_time,start_time_iso,end_time_iso,project_id,num_model_requests,user_id,"
    "api_key_id,model,batch,service_tier,input_tokens,output_tokens,input_cached_tokens,"
    "input_cache_write_tokens,input_cache_write_30m_tokens,input_cache_write_12h_tokens,"
    "input_uncached_tokens,input_text_tokens,output_text_tokens,input_cached_text_tokens,"
    "input_audio_tokens,input_cached_audio_tokens,output_audio_tokens"
)
# 2026-09-20 00:00 UTC = 1789862400
OPENAI_USAGE = "\n".join(
    [
        OPENAI_USAGE_HEADER,
        "1789862400,1789948800,2026-09-20T00:00:00+00:00,2026-09-21T00:00:00+00:00,proj_A,13.0,,"
        "key_A,gpt-5-mini-2025-08-07,,,11512.0,12686.0,0.0,0.0,0.0,0.0,11512.0,11512.0,12686.0,"
        "0.0,0.0,0.0,0.0",
        "1789948800,1790035200,2026-09-21T00:00:00+00:00,2026-09-22T00:00:00+00:00,,,,,,,,,,,,,,,,,,,,",
    ]
)

OPENAI_COST_HEADER = (
    "start_time,end_time,start_time_iso,end_time_iso,amount_value,amount_currency,quantity,"
    "quantity_unit,line_item,user_id,api_key_id,project_id,organization_id,project_name,"
    "organization_name,user_email,api_source"
)
OPENAI_COST = "\n".join(
    [
        OPENAI_COST_HEADER,
        "1789862400,1789948800,2026-09-20T00:00:00,2026-09-21T00:00:00,0E-6176,usd,0.0,tokens,"
        '"gpt-5-mini-2025-08-07, cached input",,,proj_A,org-X,Project A,Acme,,',
        "1789862400,1789948800,2026-09-20T00:00:00,2026-09-21T00:00:00,"
        '0.002878000000000000000000,usd,11512.0,tokens,"gpt-5-mini-2025-08-07, input",,,proj_A,'
        "org-X,Project A,Acme,,",
        "1789862400,1789948800,2026-09-20T00:00:00,2026-09-21T00:00:00,"
        '0.02537200000000000000000,usd,12686.0,tokens,"gpt-5-mini-2025-08-07, output",,,proj_A,'
        "org-X,Project A,Acme,,",
        "1789862400,1789948800,2026-09-20T00:00:00,2026-09-21T00:00:00,0.005,usd,2.0,calls,"
        "file search tool calls,,,proj_A,org-X,Project A,Acme,,",
    ]
)
OPENAI_COST_UNGROUPED = "\n".join(
    [
        OPENAI_COST_HEADER,
        "1789862400,1789948800,2026-09-20T00:00:00,2026-09-21T00:00:00,0.03,usd,,,,,,,org-X,,Acme,,",
    ]
)

ANTHROPIC_USAGE_HEADER = (
    "usage_date_utc,model_version,api_key,workspace,usage_type,context_window,"
    "usage_input_tokens_no_cache,usage_input_tokens_cache_write_5m,"
    "usage_input_tokens_cache_write_1h,usage_input_tokens_cache_read,usage_output_tokens,"
    "web_search_count,inference_geo,speed"
)
ANTHROPIC_USAGE = "\n".join(
    [
        ANTHROPIC_USAGE_HEADER,
        "2026-09-21,claude-sonnet-5,key-one,Default,standard,≤ 200k,2498,0,0,0,1032,0,global,standard",
        # A second row for the same grain (another context-window bucket) must be summed.
        "2026-09-21,claude-sonnet-5,key-one,Default,standard,> 200k,2,0,0,0,0,0,global,standard",
        "2026-09-22,claude-sonnet-5-5,key-one,Default,standard,≤ 200k,100,0,0,0,10,0,global,fast",
    ]
)

ANTHROPIC_COST_HEADER = (
    "usage_date_utc,model,workspace,api_key,usage_type,context_window,token_type,cost_usd,"
    "list_price_usd,cost_type,inference_geo,speed,api_key_id,api_key_status"
)
ANTHROPIC_COST = "\n".join(
    [
        ANTHROPIC_COST_HEADER,
        "2026-09-21,--,Default,key-one,--,--,--,0.00,0.00,web_search,--,--,apikey_01ONE,active",
        "2026-09-21,Claude Sonnet 5,Default,key-one,message,≤ 200k,input_no_cache,0.00,0.00,token,"
        "global,standard,apikey_01ONE,active",
        "2026-09-21,Claude Sonnet 5,Default,key-one,message,≤ 200k,output,0.01,0.01,token,"
        "global,standard,apikey_01ONE,active",
    ]
)
