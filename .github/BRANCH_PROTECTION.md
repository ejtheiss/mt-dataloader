# Protect `main` (owner / admin only)

GitHub’s **“Your main branch isn’t protected”** banner is fixed in **repository settings**.  
**Collaborators** with only *write* access **cannot** set this via API or UI—only **Owner** or **Admin** role can.

## Option A — Classic branch protection (simple)

1. Open: `https://github.com/ejtheiss/mt-dataloader/settings/branches`
2. **Add branch protection rule** (or **Add rule** under Branch protection).
3. **Branch name pattern:** `main`
4. Enable:
   - **Require a pull request before merging** (optional but recommended for pairs)
   - **Require status checks to pass before merging**
     - **Require branches to be up to date before merging** (strict) — optional
     - Under **Status checks that are required**, add: **`CI / test`**  
       (appears after the [CI workflow](workflows/ci.yml) has run at least once on a PR or on `main`)
   - **Do not allow bypassing the above settings** (optional)
5. Under **Rules applied to everyone including administrators** (optional): lock force-push/delete for admins too.
6. Save.

## Option B — Repository rulesets (newer UI)

1. `https://github.com/ejtheiss/mt-dataloader/settings/rules`
2. **New ruleset** → target **Branches**, include `main`.
3. Add rules: **Block force pushes**, **Require status checks** → select **`CI / test`** (GitHub Actions).

## After merging the CI PR

If `main` has never run **CI**, the **`CI / test`** check may not appear in the dropdown until you push/merge once or open a PR that runs the workflow.

## API (admin token only)

If you have a PAT with `admin:org` / full repo admin, you can automate later; collaborators typically see **404** on protection endpoints.
