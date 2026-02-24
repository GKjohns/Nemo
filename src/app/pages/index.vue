<script setup lang="ts">
definePageMeta({ layout: 'landing' })

const heroLinks = [{
  label: 'Start exploring',
  to: '/sessions',
  trailingIcon: 'i-lucide-arrow-right',
  size: 'xl' as const
}, {
  label: 'Learn more',
  to: '#how-it-works',
  size: 'xl' as const,
  color: 'neutral' as const,
  variant: 'subtle' as const
}]

const features = [{
  icon: 'i-lucide-upload',
  title: 'Connect your data',
  description: 'Upload a CSV, point Nemo at a Postgres database, or connect a SQLite file. Nemo profiles your schema automatically — columns, types, distributions, and relationships.'
}, {
  icon: 'i-lucide-telescope',
  title: 'State your hypothesis',
  description: 'Give Nemo a starting prompt: "The redesign is causing churn" or "Revenue dipped in Q3." It uses that as its compass, then dives deeper than you expected.'
}, {
  icon: 'i-lucide-workflow',
  title: 'Watch the graph grow',
  description: 'Nodes appear in real time as Nemo runs queries and forms insights. Edges connect related findings. You can watch it work or walk away and come back to a finished map.'
}, {
  icon: 'i-lucide-eye',
  title: 'Full transparency',
  description: 'Every query, every chart, every reasoning step is visible. Click any node to see exactly what Nemo did. Not a black box — an analyst working in front of you.'
}, {
  icon: 'i-lucide-git-branch',
  title: 'Follows threads automatically',
  description: 'When Nemo finds something interesting, it keeps going. It generates follow-up queries, spots conflicts in the evidence, and resolves ambiguity before surfacing its synthesis.'
}, {
  icon: 'i-lucide-anchor',
  title: 'Surfaces when done',
  description: 'When Nemo finishes, you get a structured narrative: what is true, what is false, what is uncertain, and what you should look at next.'
}]

const ctaLinks = [{
  label: 'Open dashboard',
  to: '/sessions',
  trailingIcon: 'i-lucide-arrow-right',
  color: 'neutral' as const
}]
</script>

<template>
  <div>
    <UPageHero
      title="Your data has a story. Nemo finds it."
      description="Nemo is an autonomous data exploration engine. Give it a dataset and a hypothesis — or just a hunch — and it dives. It runs queries, forms insights, and surfaces patterns you didn't think to look for."
      :links="heroLinks"
    />

    <UPageSection
      id="how-it-works"
      title="Not just answers — a map of evidence"
      description="A good analyst follows the thread until they find the question that should have been asked. Nemo does that systematically, building a connected knowledge graph along the way."
      :features="features"
    />

    <UPageSection>
      <UPageCTA
        title="Ready to let Nemo dive?"
        description="Stop writing one-off queries and start building a living map of what your data actually means."
        variant="subtle"
        :links="ctaLinks"
      />
    </UPageSection>
  </div>
</template>
