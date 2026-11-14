"""Allowlisted diagnostics only: never retain validation inputs, messages or context."""


class ScoringResponseError(RuntimeError):
    pass


def validation_failure(error, stage):
    from job_radar.application_models import MatchEvaluation, MatchDimensions, FitEvidence
    fields = set(MatchEvaluation.model_fields) | set(MatchDimensions.model_fields) | set(FitEvidence.model_fields)
    kinds = {'json_invalid', 'missing', 'extra_forbidden', 'literal_error', 'string_too_long',
             'too_long', 'int_parsing', 'int_type', 'string_type', 'list_type', 'model_type',
             'greater_than_equal', 'less_than_equal'}
    details = []
    for entry in error.errors(include_url=False, include_context=False, include_input=False)[:8]:
        path = '.'.join(str(x) if isinstance(x, int) else x if x in fields else '?' for x in entry['loc']) or '$'
        kind = entry['type'] if entry['type'] in kinds else 'validation_error'
        details.append(f'{path}:{kind}')
    return ScoringResponseError(f'AI validation stage={stage}; ' + ','.join(details))


def parse_score_response(responses, **kwargs):
    from pydantic import ValidationError
    try:
        # Raw wrapper retains the envelope when SDK Pydantic parsing raises.
        raw_api = getattr(responses, 'with_raw_response', None)
        if raw_api is None:  # Injected provider adapters used by offline clients.
            return responses.parse(**kwargs)
        raw = raw_api.parse(**kwargs)
        try:
            envelope = raw.http_response.json()
            if not isinstance(envelope, dict):
                raise ValueError
        except ValueError:
            raise ScoringResponseError('AI response stage=envelope; invalid_json') from None
        status = envelope.get('status')
        if status != 'completed':
            details = envelope.get('incomplete_details')
            reason = details.get('reason') if isinstance(details, dict) else None
            reason = reason if reason in {'max_output_tokens', 'content_filter'} else 'unknown'
            raise ScoringResponseError(f'AI response stage=envelope; not_completed; reason={reason}')
        response = raw.parse()
        if response.output_parsed is None:
            raise ScoringResponseError('AI response stage=parsed; missing_output_or_refusal')
        return response
    except ValidationError as error:
        raise validation_failure(error, 'sdk_parse') from None
