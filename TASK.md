# Take-home: Build a minimal web gym for RL agents

**Budget:** around 8-10 focused hours,  **Submission:** a short writeup. We'll then book a 60-minute call to walk through github  code together. **On your code:** we don't retain it. This exercise exists so we can see how you reason through an open-ended problem.

---

## The problem

Our product is a web gym — a sandboxed, resettable browser environment that an RL trainer drives at scale to teach a policy how to click, type, and navigate its way through real-world tasks. Customers — typically AI labs and applied teams shipping computer-use agents — spin up many parallel instances of one of our gyms and run rollouts against it.

For that to be useful, a gym needs a handful of properties:

- **Realism**, so that skills the agent learns inside transfer outside.  
- **Fast, deterministic resets**, so the same seed reliably produces the same starting state.  
- **Verifiable reward**, decided by inspecting backend state rather than by an LLM judge or by reading rendered HTML.  
- **Parallel safety**, so many instances can run on one host without interfering with each other.  
- **Extensibility**, so adding a new task doesn't require rewriting the environment.

You'll build a small version of one. The target domain is e-commerce — a stripped-down shopping site used as the substrate for a few well-defined tasks.

---

## What we'll be paying attention to

Roughly six things, in approximate order of weight:

1. **Scope choices.** What you chose to build, and just as importantly, what you chose to leave out.  
2. **Interface design.** Whether a teammate could add a new task tomorrow without reading your source, and whether a customer's trainer could plug in cleanly.  
3. **Reward robustness.** Whether an agent that almost completes a task can still pass. Ideally, it shouldn't.  
4. **Parallel correctness.** Whether parallelism actually holds up under contention, not just in the happy path.  
5. **Code quality.** Readable, modestly tested, no obvious foot-guns. We're not expecting production hardening in this budget.  
6. **Communication.** Whether the writeup gives us an honest picture of what's solid, what's rough, and what you'd revisit.

Two thoughtful engineers will make different choices on this exercise, and that's expected. We're reading the reasoning behind your choices, not looking for a specific answer.

---

## What to build

### The site

A small e-commerce app running in Docker — product listing, product detail, cart, checkout, and an order-confirmation page. At least 10 seeded products. SQLite is fine. You're welcome to adapt an existing open-source storefront or write your own; either works, just tell us why you picked what you picked. Unstyled HTML is completely fine — we're not scoring CSS.

### The env

A Python class with a Gymnasium-compatible interface — `reset(seed=None)` and `step(action)`. The observation should include the current URL, the DOM (or accessibility tree), and a screenshot. The action space should support at minimum click, type, scroll, and navigate. Playwright is our default suggestion for the browser driver, though Selenium is fine if you have a reason to prefer it.

### Reset

Deterministic given a seed — same seed should yield the same product catalog, pre-existing orders, and coupons. Try to keep reset latency under 3 seconds on a normal laptop. If you run into places where determinism leaks (timestamps, autoincrement IDs, anything that touches `now()`), it's worth a few sentences in the writeup about how you handled it.

### Three tasks

We're going to specify these so that submissions stay comparable, and because picking good tasks is a separate skill we'd rather explore in the follow-up call. You're welcome to add a fourth of your own if time permits.

- **`buy_cheapest_in_category`** — *"Buy the cheapest item in the 'Electronics' category and ship it to `123 Main St, Springfield, IL 62701`."* This covers the happy path: filter, select, checkout.  
- **`apply_coupon_with_quantity`** — *"Add 2 units of `SKU-E7421` to the cart, apply coupon `SAVE10`, and complete checkout."* This stresses precision — it should catch an agent that gets the SKU but the wrong quantity, or skips the coupon.  
- **`cancel_recent_order`** — *"Cancel the most recent existing order in the account."* This exercises operating on pre-seeded state and verifying a state transition (`placed → cancelled`), rather than just checking that new state was created.

Each task should come with a verifier that reads backend state — the database or an internal app API — and shouldn't rely on an LLM as judge or on scraping rendered HTML for strings like "Thank you for your order." Both of those patterns undermine the determinism the gym is meant to provide, so we'd ask you to avoid them.

Seed data is part of the task definition. `reset()` should make sure the Electronics category is populated, `SAVE10` is a valid coupon, and a cancelable order exists in the account. How you structured this is worth a paragraph in the writeup.

### Parallel rollout demo

A script that launches at least 4 concurrent env instances, runs a baseline policy (random clicks, a scripted oracle, or both — your call) for some episodes per task, and prints success rates. Concurrent instances shouldn't share mutable state; if they do, you'll usually notice because verifiers start passing for the wrong reasons.

### Writeup

Roughly 600–1200 words, with four sections:

- **Architecture in one paragraph**, just to orient us.  
- **3–5 decisions you spent the most time on.** For each: what you picked, what else you considered, and why you landed where you did. Likely candidates include the element-identification scheme, the reset strategy, the parallelism approach, and the verifier interface.  
- **What you'd do with more time.** The more specific, the better. Something like *"build a synthetic task-generation pipeline that takes the product catalog and emits (description, verifier, seed-config) triples, validated by running the scripted oracle against each"* is more useful to us than a general direction.  
- **What you cut, and any bugs you know are still in there.** Honest scoping is a skill we genuinely care about — acknowledged omissions are a stronger signal than polished ones.

---

## A few ground rules

**AI tools are welcome.** Claude, Cursor, Copilot — please use whatever speeds you up. In the follow-up call we'll ask about parts of the submission, and what we're looking for is that you can explain the design choices, not that you wrote every line by hand.

**Please time-box.** If you hit 10 hours and aren't finished, it's better to stop and document what's missing in `doc` than to push through to a polished version. We weight a thoughtful partial submission more highly than a complete one that quietly took 25 hours.

**Please build this from scratch rather than starting from an existing web-gym repo** (BrowserGym, WebArena forks, etc.). We'd like to see how you'd design it yourself.

**No proprietary code** from previous roles, please — we'd rather see something rougher that's clearly yours.

---

## Things we'd suggest skipping

These tend to consume time without telling us much, so feel free to leave them aside:

- A real RL training loop. A baseline policy is plenty.  
- Auth, user accounts, real payments, visual polish.  
- Cross-browser support — one Chromium target is sufficient.  
- Plugin systems or abstraction layers that only have one concrete implementation. It's usually better to keep things concrete and abstract later if needed.

---

## After you submit

Once we've reviewed your submission, we'll book a 60-minute screenshare. Three parts:

- **First 10 minutes:** you walk us through the repo, treating us as new teammates joining the project.  
- **Next 30 minutes:** we pair on an extension together — possibly a new action type, a new task, or a throughput improvement. We'll pick based on what your submission suggests would be most interesting to explore. Your machine and tools.  
- **Last 20 minutes:** an open design discussion. Some examples of what we might ask:  
  - How would you scale this gym to 500 concurrent rollouts on a single host?  
  - Where do you think this gym would fail to teach skills that transfer to the open web?  
  - If we asked you to add a fourth task right now, what would it be, and why?  
  - How would you generate 10,000 tasks without authoring each by hand?

There's no need to prepare answers — we'd rather see how you think live than rehearse with you.

---

If you find yourself blocked or you have a question about scope, please text me at 4703439233\. Asking is genuinely not a negative signal — we'd much rather unblock you than have everyone interpret the same ambiguity differently.

We're looking forward to seeing what you build.  