<script setup>
import { onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const rows = ref([])
const shifts = ref([])
const err = ref('')
const msg = ref('')

async function refresh() {
  rows.value = await getJson('/api/batches')
}

onMounted(async () => {
  try {
    rows.value = await getJson('/api/batches')
    shifts.value = await getJson('/api/shifts')
  } catch (e) {
    err.value = String(e.message || e)
  }
})

async function settle(b) {
  err.value = ''
  msg.value = ''
  try {
    const out = await postJson(`/api/batches/${b.id}/settle`, {
      shift_id: b.owner_shift_id,
    })
    msg.value = `批次 ${out.biz_key} 已结清`
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
}

async function reassign(b, toId) {
  err.value = ''
  msg.value = ''
  try {
    const out = await postJson(`/api/batches/${b.id}/reassign`, { to_shift_id: Number(toId) })
    msg.value = `批次 ${out.biz_key} 已改归属`
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
}
</script>

<template>
  <div>
    <h1>在途批次</h1>
    <p class="lead">批次归属当前值班班次；交班完成后，在途批次应整批过户到接班方。</p>
    <p class="lead">
      结清与改归属都要求操作班次对该批次责任区具备资格；原归属与目标班次任一方无资格即整单拒绝，并写明资格。
    </p>
    <p v-if="err" class="msg">{{ err }}</p>
    <p v-if="msg" class="msg ok">{{ msg }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>业务键</th><th>标题</th><th>责任区</th><th>归属班次</th><th>状态</th><th>备注</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in rows" :key="b.id">
            <td>{{ b.biz_key }}</td>
            <td>{{ b.title }}</td>
            <td>{{ b.zone_name || '—' }} <span class="sub">{{ b.zone_code }}</span></td>
            <td>{{ b.owner_shift_name || '—' }}</td>
            <td><span class="tag" :class="{ on: b.status === '在途' }">{{ b.status }}</span></td>
            <td>{{ b.note || '—' }}</td>
            <td class="ops">
              <template v-if="b.status === '在途'">
                <button class="mini" @click="settle(b)">结清</button>
                <select class="mini-select" @change="reassign(b, $event.target.value); $event.target.value = ''">
                  <option value="">改归属…</option>
                  <option v-for="s in shifts" :key="s.id" :value="s.id" :disabled="s.id === b.owner_shift_id">
                    {{ s.name }}
                  </option>
                </select>
              </template>
              <span v-else class="hint">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
