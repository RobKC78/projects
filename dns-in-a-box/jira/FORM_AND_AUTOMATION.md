# Jira Service Management DNS change form

Create a dedicated request type such as **DNS record change**. Use a dedicated Jira work type for the integration and bind its underlying ID, project key, approved status ID, and eight custom field IDs in [config/jira.json](../config/jira.json). Link each form question used by the automation to the corresponding Jira field so the REST API can read it. Keep portal access limited to authorized requesters for internal DNS. To collect your actual IDs, inspect a pilot issue with `GET /rest/api/3/issue/<key>?fields=project,issuetype,status` and look up the custom field names and IDs using `GET /rest/api/3/field`.

## Fields the pipeline consumes

| Field | Jira field type | Required when | Guidance |
| --- | --- | --- | --- |
| Summary | System summary | Always | Short description of the change |
| DNS operation | Single select | Always | `Create`, `Update`, `Delete` |
| DNS zone and view | Single select | Always | Use the exact allowlist key, such as `lab-internal`. Include distinct choices for internal/external and environments. This one field maps to the zone, view, and target servers |
| DNS owner FQDN | Short text | Always | Full owner, e.g. `app.lab.example.net`. For PTR supply the reverse owner, e.g. `44.2.0.192.in-addr.arpa` |
| DNS record type | Single select | Always | `A`, `AAAA`, `CNAME`, `TXT`, `MX`, `SRV`, `PTR`, `CAA`. Show only types enabled for the intended pilot |
| DNS current state | Paragraph | Update and delete | Exact BAM object ID, current explicit TTL in seconds, and current data as JSON. DNS team can populate this during triage |
| DNS desired state | Paragraph | Create and update | New TTL in seconds and desired data as JSON. Keep it blank for delete |
| Window start UTC | Short text | When required by zone policy or CAB | UTC format `YYYY-MM-DDTHH:MM:SSZ` |
| Window end UTC | Short text | When a start time is provided | Same format. Use a window of up to 24 hours. Both values may be blank when policy allows |
| Issue key, project, work type, approved status | Jira metadata | Always | Jira supplies these values |

## Fields for intake and approvals

These fields stay in Jira for reviewers. Mark mandatory items in your request form or transition validation.

| Field | Suggested requirement |
| --- | --- |
| Requester and contact | Jira reporter plus alternate contact if the requester is acting on behalf of another team |
| Owning team, app/service, on-call | Required for ownership, testing, and rollback |
| Business reason and desired outcome | Required |
| Business impact, environment, and visibility | Required. Include internal/public scope, production impact, and sites or user groups affected |
| Change/CAB record and approved window | Required for production according to policy. Include customer-facing downtime if expected |
| Dependencies and affected clients | Required for update/delete. Include aliases, app bindings, certificates, email routing, and other DNS clients as applicable |
| Validation plan and test owner | Required. Include the query or application check to run after publication |
| Rollback plan and decision owner | Required for production, updates, and deletions. Capture old TTL, old targets, and conditions for rollback |
| DNS approver, service owner approval, Security/CAB approval | Resolve from trusted Jira workflow policy, ownership data, and zone classification. Capture actual Jira approvers and approvals |
| IPAM assignment reference | Required for A/AAAA when the address is newly provisioned. The address must already exist in BAM before the pipeline runs |
| Forward/reverse coordination | Required if a PTR is needed. Link the companion ticket and agree execution order |
| External target approval or proof of control | Required where policy calls for it, for example external CNAME, mail, and certificate validation records |
| Retirement confirmation and monitoring | Required for delete. Confirm the service owner retired it and identify what will be checked after removal |
| Implementation result | System-managed. Internal comments record the PR, verification result and workflow run |

## Type-specific data

For desired state, wrap the applicable object below as `{"ttl":300,"data":<object>}`. For current state also add the exact numeric BAM object ID as `"id"`. For example, `{"id":3100,"ttl":600,"data":{"target":"old.lab.example.net","target_id":3101}}`. Set explicit TTLs between 60 and 86400 seconds for create/update. These examples use reserved DNS names and addresses.

| Type | Data object | Required details |
| --- | --- | --- |
| A | `{"addresses":["192.0.2.44"]}` | 1–16 existing IPv4 addresses in the BAM configuration |
| AAAA | `{"addresses":["2001:db8::44"]}` | 1–16 existing IPv6 addresses in the BAM configuration |
| CNAME | `{"target":"build01.lab.example.net","target_id":3101}` | Canonical FQDN and existing same-view Host/External Host object ID |
| TXT | `{"text":"verification=sample"}` | One printable ASCII string, 1–255 bytes. Keep secrets and private keys out of the ticket and Git |
| MX | `{"target":"mail.lab.example.net","target_id":3102,"priority":10}` | Mail exchanger FQDN, BAM ID and preference |
| SRV | `{"target":"ldap.lab.example.net","target_id":3103,"priority":10,"weight":20,"port":389}` | Owner starts with `_service._tcp` or `_service._udp`, with service priority, weight, port, target and BAM ID |
| PTR | `{"target":"app.lab.example.net"}` | Reverse owner in the reverse zone and destination hostname |
| CAA | `{"flags":0,"tag":"issue","value":"ca.example.net"}` | Flags 0 or 128, tag `issue`, `issuewild`, or `iodef`, and value |

The shipped field mapping uses two JSON paragraphs. For a requester-friendly portal, use conditional questions for TTL, IPs, targets, priorities, ports and the other fields above. Have Jira Automation or a triage step populate the two linked JSON fields before approval. Keep raw and generated states consistent, and route meaningful edits back for review. This package provides the linked JSON contract and dispatch rule template. The conditional form builder and field IDs are site setup.

## Configure the Automation rule

1. Use trigger **Work item transitioned** into your dedicated approved status, such as **Approved for DNS GitOps**. Restrict the transition to eligible approvers or an approved JSM workflow. Limit the rule to your DNS project, dedicated work type, and DNS request type. Re-fetch issue data after any generated fields are written.
2. Add **Send web request**, method **POST**, URL `https://api.github.com/repos/OWNER/REPO/actions/workflows/intake.yml/dispatches`. Set headers `Accept: application/vnd.github+json`, `Content-Type: application/json`, and hidden `Authorization: Bearer <dispatch credential>`. Restrict rule edit access and rotate the credential according to your policy.
3. Use custom data from [dispatch.json](dispatch.json). Enable waiting for the web request response and treat HTTP 2xx as accepted. The response confirms GitHub accepted the trigger. GitHub intake fetches and validates the real ticket, then posts the PR link as an internal Jira comment. A dispatch error should route to the DNS queue.
4. Keep the request approved until the deployment job reports authoritative verification. Revoke approval if technical inputs change. For a changed request with an existing pull request, close that PR and submit a new ticket. If a new PR is missing, inspect the GitHub intake run and Jira rule audit log before retrying.

[Atlassian Automation web requests](https://support.atlassian.com/cloud-automation/docs/what-third-party-applications-can-i-integrate-with/) and [GitHub workflow dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event).
