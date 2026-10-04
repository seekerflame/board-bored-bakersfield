/* Board Bored analytics settings. The ONE line Nick edits: paste the PostHog project key below.
 * Empty = analytics stays completely off (no script is loaded, nothing is sent).
 * Get the key from PostHog -> Project settings -> "Project token" (starts with phc_).
 * Keep host on the US cloud unless the PostHog project was created in the EU region.
 * After editing, bump VERSION in sw.js so installed apps pick up the change. */
window.BB_ANALYTICS = {
  key: "",
  host: "https://us.i.posthog.com",
  // only these hostnames ever send anything (local testing and previews stay silent)
  hosts: ["seekerflame.github.io"]
};
