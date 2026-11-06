#!/usr/bin/env bash
# Submit an Agent Registry record for approval, and approve it if APPROVE=true.
# Run by Terraform (terraform_data.submit) after the record is created or updated.
#
# Env: REGISTRY_ID, RECORD_ID, APPROVE (true|false); AWS_REGION / AWS_PROFILE optional.
# Needs an AWS CLI v2 recent enough to have `aws agent-registry-control`.
set -euo pipefail

: "${REGISTRY_ID:?REGISTRY_ID is required}"
: "${RECORD_ID:?RECORD_ID is required}"

if ! aws agent-registry-control help >/dev/null 2>&1; then
  echo "error: this AWS CLI has no 'agent-registry-control' command; upgrade AWS CLI v2" >&2
  exit 1
fi

record() {
  aws agent-registry-control get-registry-record \
    --registry-id "$REGISTRY_ID" --record-id "$RECORD_ID" \
    --query "$1" --output text
}

# Wait while the service is still processing a create or update.
status=$(record status)
for _ in $(seq 1 60); do
  case "$status" in
    CREATING | UPDATING) sleep 5; status=$(record status) ;;
    *) break ;;
  esac
done

case "$status" in
  CREATE_FAILED | UPDATE_FAILED)
    echo "error: record $RECORD_ID is $status: $(record statusReason)" >&2
    exit 1
    ;;
  CREATING | UPDATING)
    echo "error: record $RECORD_ID is still $status after 5 minutes" >&2
    exit 1
    ;;
  DRAFT)
    status=$(aws agent-registry-control submit-registry-record-for-approval \
      --registry-id "$REGISTRY_ID" --record-id "$RECORD_ID" \
      --query status --output text)
    echo "record $RECORD_ID submitted for approval -> $status"
    ;;
esac

if [ "$status" = "PENDING_APPROVAL" ] && [ "${APPROVE:-false}" = "true" ]; then
  status=$(aws agent-registry-control update-registry-record-status \
    --registry-id "$REGISTRY_ID" --record-id "$RECORD_ID" \
    --status APPROVED --status-reason "Approved by Terraform" \
    --query status --output text)
  echo "record $RECORD_ID approved"
fi

echo "record $RECORD_ID status: $status"
if [ "$status" = "PENDING_APPROVAL" ]; then
  echo "note: approve it in the console, set approve = true, or enable auto_approve on the registry"
fi
