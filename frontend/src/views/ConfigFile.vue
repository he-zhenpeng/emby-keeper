<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { DocumentTextIcon } from '@heroicons/vue/24/outline'
import UiCard from '@/components/ui/UiCard.vue'
import UiButton from '@/components/ui/UiButton.vue'
import { useAuthStore } from '@/stores/auth'
import { logger } from '@/utils/logger'

const auth = useAuthStore()
const content = ref('')
const savedContent = ref('')
const configPath = ref('')
const loading = ref(true)
const saving = ref(false)
const pageError = ref('')
const saveMessage = ref('')

const isDirty = computed(() => content.value !== savedContent.value)

const loadConfig = async () => {
  loading.value = true
  pageError.value = ''
  saveMessage.value = ''
  try {
    const data = await auth.request<{ content: string, path: string }>('/config/raw')
    content.value = data.content || ''
    savedContent.value = data.content || ''
    configPath.value = data.path || ''
  } catch (error) {
    logger.error('Failed to load config file', error)
    pageError.value = logger.message(error, '配置文件加载失败')
  } finally {
    loading.value = false
  }
}

const saveConfig = async () => {
  if (!isDirty.value) return

  saving.value = true
  pageError.value = ''
  saveMessage.value = ''
  try {
    await auth.request('/config/raw', {
      method: 'POST',
      body: JSON.stringify({ content: content.value }),
    })
    savedContent.value = content.value
    saveMessage.value = '已保存并生效，下次任务将读取新配置。'
  } catch (error) {
    logger.error('Failed to save config file', error)
    pageError.value = logger.message(error, '配置文件保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadConfig()
})
</script>

<template>
  <div class="space-y-8">
    <section class="overflow-hidden rounded-[30px] border border-slate-200/70 bg-gradient-to-br from-slate-50 via-white to-slate-100 shadow-[0_24px_80px_-40px_rgba(15,23,42,0.35)]">
      <div class="px-4 py-5 sm:px-6 sm:py-6 xl:px-8">
        <div class="space-y-3">
          <div class="inline-flex items-center gap-2 rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold uppercase tracking-[0.22em] text-slate-700 ring-1 ring-slate-200">
            <DocumentTextIcon class="h-4 w-4" aria-hidden="true" />
            Config
          </div>
          <div>
            <h1 class="text-2xl font-semibold tracking-tight text-slate-950 sm:text-3xl">配置文件</h1>
            <p class="mt-2 max-w-2xl text-sm leading-6 text-slate-600">直接查看和编辑 config.toml，保存后立即生效，无需重启后端。</p>
          </div>
        </div>
      </div>
    </section>

    <UiCard class="overflow-hidden border-slate-200/70 bg-white/90">
      <template #header>
        <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h2 class="text-lg font-semibold tracking-tight text-slate-950">config.toml</h2>
            <p class="mt-1 font-mono text-xs text-slate-500">{{ configPath || '加载中…' }}</p>
          </div>
          <div class="flex shrink-0 flex-wrap items-center gap-2">
            <span v-if="isDirty" class="inline-flex items-center rounded-full bg-amber-100 px-3 py-1.5 text-xs font-medium text-amber-800 ring-1 ring-amber-200">
              有未保存的修改
            </span>
            <UiButton size="sm" variant="secondary" :disabled="loading || saving" @click="loadConfig">
              重新加载
            </UiButton>
            <UiButton size="sm" :loading="saving" :disabled="!isDirty || loading" @click="saveConfig">
              保存
            </UiButton>
          </div>
        </div>
      </template>

      <div v-if="pageError" class="mb-4 rounded-[20px] border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
        {{ pageError }}
      </div>

      <div v-if="saveMessage" class="mb-4 rounded-[20px] border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
        {{ saveMessage }}
      </div>

      <div v-if="loading" class="rounded-[24px] border border-dashed border-slate-300 bg-white/70 px-6 py-16 text-center">
        <div class="mx-auto flex h-14 w-14 items-center justify-center rounded-full bg-slate-50 text-slate-500 ring-1 ring-slate-100">
          <div class="h-7 w-7 animate-spin rounded-full border-2 border-slate-200 border-t-slate-500"></div>
        </div>
        <p class="mt-5 text-base font-medium text-slate-700">正在加载配置文件</p>
        <p class="mt-2 text-sm text-slate-500">请稍候，内容准备好后会直接显示在这里。</p>
      </div>

      <div v-else>
        <label for="config-file-editor" class="sr-only">config.toml 内容</label>
        <textarea
          id="config-file-editor"
          v-model="content"
          rows="24"
          spellcheck="false"
          placeholder="# config.toml 为空，使用全部默认配置"
          class="block w-full rounded-[18px] border-0 bg-slate-50 px-4 py-3 font-mono text-sm leading-6 text-slate-900 ring-1 ring-inset ring-slate-200 placeholder:text-slate-400 focus:ring-2 focus:ring-primary-600"
        />
        <p class="mt-3 text-xs leading-5 text-slate-500">
          保存时会先校验 TOML 格式与配置项，格式错误或配置项非法时不会保存，并会显示具体原因；注释与原有格式会原样保留。正在运行的任务不受影响。
        </p>
      </div>
    </UiCard>
  </div>
</template>
