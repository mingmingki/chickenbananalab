# Quantity: resuming an analysis interrupted by a server restart — 2026-10-15

Builds on the floor heights increment (main f635a18). Branch `feat/quantity-ops-and-stage-20261015`.

A main analysis runs in a background thread of the single web worker. Restarting the server, for example during a deploy, stops that thread. Before this change, the job then showed "interrupted" and its uploads were lost. The user had to upload everything again, and every AI step was paid again.

## What is kept (core/quantity_job_inputs.py)
- **Uploads:** when an analysis starts, its merged uploads are written to a private folder next to the job state (`var/quantity/inputs/<hash of the job id>/`). The folder is 0700 and its files are 0600.
- **Folder contents:** a record of the owner, the size and SHA-256 of each upload, and the settings the job started with: the confirmed general spec and the stages.
- **Finished AI steps:** each step is saved as soon as it returns a usable answer.
  - Steps: architectural analysis, the elevation/section review, and each structural extraction batch.
  - Steps whose AI request failed or whose answer could not be parsed are not saved, so a resume asks again.
  - A truncated answer that was repaired is saved, because asking again rarely helps.
- **Key per step:** each saved step carries a key built from:
  - the uploads;
  - the CAD summary the AI saw;
  - the pages of the batch;
  - the confirmed general spec;
  - the prompts and the model.

  A resume reuses a step only when the key is identical. After a deploy that changed any of these, the step is asked again.
- **Lifetime:**
  - The folder is removed as soon as the job stores its result (success, failure or cancel).
  - Folders of jobs that are never resumed expire after 3 days.
  - Nothing is kept when the disk would drop below 2 GB free, or when kept folders would exceed 3 GB together. The analysis still runs, but it cannot resume.

## Resuming
- **Who and when:** `POST /api/quantity/resume/` with the job id. It is admin-only and for the job's owner. It works only when the job's worker is gone and no result was stored.
- **Single worker:** the job is bound to the new worker atomically, so two clicks or two workers cannot run it twice.
- **What runs again:** the CAD parsing runs again (no AI cost). The saved AI steps are reused. When both architectural steps are saved, the architectural PDF is not rendered again. Only the remaining steps call the AI.
- **Never automatic:** a resume always starts from the owner's click.
- **Audit note:** the extraction notes record which steps were reused ("이어서 실행: …"). This note is not treated as an incomplete extraction.

## Screens
- **Progress polling:**
  - An interrupted job now reports whether it can resume and how many AI steps are saved.
  - The chat shows "분석이 중간에 멈췄어요" with an **이어서 실행** button and the usual restart button.
  - Progress then continues in the same box style.
- **Saved jobs (저장된 작업):** the list marks interrupted jobs that can resume ("중단됨 · 이어서 실행 가능"). Opening one offers the same button.

## Deploy check
`manage.py quantity_running_jobs` lists main analyses running in a live worker. It exits with status 1 when there is any, so a restart can wait for them. The deploy sequence runs it before restarting gunicorn.

## Tests
`core/test_quantity_resume.py`, 15 tests:
- **Storage:** private storage and owner binding; damaged and expired uploads; the space guard; step keys.
- **Reuse:** only unfinished batches call the AI again; a changed spec or prompt reruns everything; saved architectural steps skip the AI and the PDF rendering; failed answers are not saved.
- **Lifecycle:** kept files are removed after a result and stay after a stop.
- **Resume API:** it starts one worker; it refuses other users, members, other job kinds, finished jobs and lost uploads.
- **Command:** the running-jobs command.
