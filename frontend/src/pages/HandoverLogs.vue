<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const rows = ref([])
const snapshots = ref([])
const err = ref('')

onMounted(async () => {
  try {
    rows.value = await getJson('/api/handovers')
    snapshots.value = await getJson('/api/qualification-snapshots')
  } catch (e) {
    err.value = String(e.message || e)
  }
})

function snapshotSummary(handoverId) {
  const items = snapshots.value.filter((s) => s.handover_id === handoverId)
  if (!items.length) return '—'
  const bad = items.filter((s) => !s.qualified)
  if (!bad.length) return '全部为可'
  return bad
    .map((s) => `${s.双方}「${s.shift_name}」×「${s.zone_name}」=否`)
    .join('；')
}
</script>

<template>
  <div>
    <h1>交班记录</h1>
    <p class="lead">每单交班留档：双方班次、清单批次数、最终状态；资格按发起时快照解释。</p>
    <p v-if="err" class="msg">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>单号</th><th>交班方</th><th>接班方</th><th>批次数</th>
            <th>状态</th><th>资格快照</th><th>原因</th><th>开始</th><th>结束</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="h in rows" :key="h.id">
            <td>#{{ h.id }}</td>
            <td>{{ h.from_shift_name }}</td>
            <td>{{ h.to_shift_name }}</td>
            <td>{{ h.batch_count }}</td>
            <td>{{ h.status }}</td>
            <td class="reason">{{ snapshotSummary(h.id) }}</td>
            <td class="reason">{{ h.reason || '—' }}</td>
            <td>{{ h.started_at }}</td>
            <td>{{ h.finished_at || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
