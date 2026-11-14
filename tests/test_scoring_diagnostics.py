import json

import httpx
import pytest
from openai import OpenAI
from pydantic import ValidationError

from job_radar.ai.openai_provider import OpenAIProvider
from job_radar.ai.diagnostics import ScoringResponseError, validation_failure
from job_radar.application_models import MatchEvaluation
from job_radar.errors import error_label
from test_openai_provider import vacancy
from test_match_v31 import evaluation


def provider(text, status='completed', reason=None):
    envelope = dict(id='resp_test', object='response', created_at=0, model='gpt-5.4-nano',
                    status=status, incomplete_details={'reason': reason} if reason else None,
                    output=[{'type':'message','id':'msg_test','role':'assistant','status':status,
                             'content':[{'type':'output_text','text':text,'annotations':[]}]}],
                    usage={'input_tokens':10,'output_tokens':1200,'total_tokens':1210},
                    parallel_tool_calls=False, tool_choice='auto', tools=[])
    client = OpenAI(api_key='fake', max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200,json=envelope))))
    return OpenAIProvider('fake','gpt-5.4-nano',client=client), client


@pytest.mark.parametrize('text,status,reason,expected', [
    ('{"private": "SECRET', 'incomplete','max_output_tokens','reason=max_output_tokens'),
    ('{"private": "SECRET', 'completed',None,'sdk_parse; $:json_invalid'),
    ('{}', 'completed',None,'missing'),
    ('{}', 'incomplete','content_filter','reason=content_filter'),
])
def test_actual_sdk_failure_paths_are_redacted(text,status,reason,expected):
    instance,client=provider(text,status,reason)
    try:
        with pytest.raises(ScoringResponseError) as result:
            instance.score({'name':'SECRET'},vacancy(),False)
        assert expected in error_label(result.value)
        assert 'SECRET' not in error_label(result.value)
    finally:
        client.close()


def test_completed_schema_and_usage_survive_actual_sdk():
    instance,client=provider(evaluation().model_dump_json())
    try:
        result=instance.score({},vacancy(),False)
        assert isinstance(result.score,MatchEvaluation)
        assert result.usage.output_tokens==1200
    finally:
        client.close()


def test_field_limits_and_untrusted_keys_are_redacted():
    data=evaluation().model_dump()
    data['explanation']='SECRET'*200
    data['SECRET_KEY']='SECRET_VALUE'
    try:
        MatchEvaluation.model_validate(data)
    except ValidationError as error:
        label=error_label(validation_failure(error,'final_policy'))
    assert 'explanation:string_too_long' in label
    assert '?:extra_forbidden' in label
    assert 'SECRET' not in label


def test_wire_schema_is_closed_and_required():
    from openai.lib._parsing._responses import type_to_text_format_param
    schema=type_to_text_format_param(MatchEvaluation)['schema']
    def visit(node):
        if isinstance(node,dict):
            if node.get('type')=='object':
                assert node['additionalProperties'] is False
                assert set(node['required'])==set(node['properties'])
            for value in node.values():
                visit(value)
        elif isinstance(node,list):
            for value in node:
                visit(value)
    visit(schema)
    assert len(json.dumps(schema)) < 32768
