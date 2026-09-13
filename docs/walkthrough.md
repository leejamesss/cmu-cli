# Try a coursework workflow without an account

Run these after the [source installation](../README.md):

```sh
cmu-cli demo
cmu-cli demo --json
```

1. Three synthetic assignments represent submitted, not submitted and unknown states.
2. The real sync writes private metadata and readable assignment/announcement indexes.
3. A synthetic homework material is placed in its numbered homework folder.
4. A repeat sync reuses the verified bytes; its fetch callback raises if invoked.
5. Temporary storage is deleted. The JSON retains the generated text and relative paths for inspection, not persistent files.

## Executed output

The following `data` fields were captured from the demo, not a live course. Envelope `retrieved_at` varies; this selected payload is reproducible.

```json
{
  "synthetic": true,
  "network_used": false,
  "statuses": [
    "downloaded",
    "unchanged"
  ],
  "file": "02_作业/Assignment_01/hw1.txt",
  "assignments": [
    {
      "name": "Read the syllabus",
      "submitted": true
    },
    {
      "name": "Practice exercise",
      "submitted": false
    },
    {
      "name": "Project proposal",
      "submitted": null
    }
  ],
  "exports": [
    ".cmucw/announcements.json",
    ".cmucw/assignments.json",
    ".cmucw/download_manifest.json",
    ".cmucw/files.json",
    ".cmucw/last_sync.json",
    ".cmucw/modules.json",
    "00_课程信息/Canvas通知索引.md",
    "02_作业/Assignment_01/hw1.txt",
    "02_作业/Canvas作业索引.md"
  ]
}
```

The generated assignment index (Chinese category labels):

```text
# DEMO-101 Canvas 作业索引

由 `cmu-cli sync` 生成。截止时间时区：America/New_York。

## Read the syllabus
- 截止：未设置
- 状态：已提交
- 分值：未设置
- 链接：
- 说明：无

## Practice exercise
- 截止：未设置
- 状态：未提交
- 分值：未设置
- 链接：
- 说明：无

## Project proposal
- 截止：未设置
- 状态：未知
- 分值：未设置
- 链接：
- 说明：无

```

`已提交` means submitted, `未提交` means not submitted, and `未知` means unknown—not a missing submission. A workflow marked graded can count as submitted without a submitted timestamp; raw Canvas submission data should remain authoritative for grading/excusal distinctions.

## Move to your own course

Follow [configuration](configuration.md) and [authorization](auth.md), then the authorized command examples in the [README](../README.md). The demo does not validate real credentials, service coverage, deadlines or provider permission. It does not submit work, and its synthetic data is never blended into your configured workspace.
