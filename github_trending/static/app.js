/**
 * GitHub Trending Tracker - Vue 3 Application
 * CDN加载，免构建
 */
const { createApp, ref, reactive, computed, onMounted, watch } = Vue;

const app = createApp({
    setup() {
        // 状态
        const tab = ref('today');
        const loading = ref(false);
        const error = ref('');
        const todayData = reactive({ date: '', projects: [] });
        
        // 历史
        const historyLoading = ref(false);
        const historyData = ref([]);
        
        // 报告
        const reportLoading = ref(false);
        const todayReport = ref(null);

        // 格式化数字
        function formatNum(n) {
            if (!n) return 0;
            n = parseInt(n);
            if (n >= 10000) return (n / 10000).toFixed(1) + 'w';
            if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
            return n.toString();
        }

        // 仓库详情辅助函数
        function getDetail(p, key) {
            if (!p || !p.details) return null;
            const val = p.details[key];
            return val !== undefined && val !== null && val !== '' ? val : null;
        }
        function hasDetails(p) {
            return p && p.details && Object.keys(p.details).length > 0;
        }
        function hasLangPct(p) {
            const lp = getDetail(p, 'language_pct');
            return lp && Object.keys(lp).length > 0;
        }
        // 语言颜色映射
        function getLangColor(lang) {
            const colors = {
                'Python': '#3572A5', 'JavaScript': '#F7DF1E', 'TypeScript': '#3178C6',
                'Java': '#B07219', 'Go': '#00ADD8', 'Rust': '#DEA584', 'C': '#555555',
                'C++': '#F34B7D', 'C#': '#178600', 'PHP': '#4F5D95', 'Ruby': '#701516',
                'Shell': '#89E051', 'HTML': '#E34F26', 'CSS': '#563D7C',
                'Vue': '#4FC08D', 'Swift': '#F05138', 'Kotlin': '#A97BFF',
                'Dart': '#00B4AB', 'Lua': '#000080', 'R': '#198CE7',
                'Objective-C': '#438EFF', 'Scala': '#C22D40', 'Perl': '#0298C3',
            };
            return colors[lang] || '#8B8B8B';
        }

        // 简单Markdown渲染
        function renderMarkdown(text) {
            if (!text) return '';
            return text
                .replace(/### (.+)/g, '<h3>$1</h3>')
                .replace(/## (.+)/g, '<h2>$1</h2>')
                .replace(/# (.+)/g, '<h2>$1</h2>')
                .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
                .replace(/\*(.+?)\*/g, '<em>$1</em>')
                .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>')
                .replace(/\n/g, '<br>');
        }

        // 加载今日数据
        async function loadToday() {
            loading.value = true;
            error.value = '';
            try {
                const res = await fetch('/api/trending');
                const data = await res.json();
                if (data.error) {
                    error.value = data.error;
                } else {
                    todayData.date = data.date;
                    todayData.projects = data.projects.map(p => ({
                        ...p,
                        _tempRating: 0,
                        _hoverRating: 0,
                        _comment: '',
                        _rated: false,
                        _userRating: null,
                        _userComment: '',
                        _showDetails: false,
                    }));
                }
            } catch (e) {
                error.value = '连接服务器失败: ' + e.message;
            } finally {
                loading.value = false;
            }
        }

        // 刷新数据
        async function refreshData() {
            loading.value = true;
            try {
                await fetch('/api/refresh', { method: 'POST' });
                await loadToday();
            } catch (e) {
                error.value = '刷新失败: ' + e.message;
            } finally {
                loading.value = false;
            }
        }

        // 提交评分
        async function submitRating(project) {
            if (!project._tempRating) return;
            try {
                const res = await fetch('/api/rate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        project_id: project.id,
                        rating: project._tempRating,
                        comment: project._comment || '',
                    })
                });
                const data = await res.json();
                if (data.success) {
                    project._rated = true;
                    project._userRating = project._tempRating;
                    project._userComment = project._comment;
                }
            } catch (e) {
                alert('提交失败: ' + e.message);
            }
        }

        // 加载历史
        async function loadHistory() {
            historyLoading.value = true;
            try {
                const res = await fetch('/api/history');
                const data = await res.json();
                historyData.value = (data.history || []).map(d => ({
                    ...d,
                    _expanded: false,
                }));
            } catch (e) {
                console.error('History load error:', e);
            } finally {
                historyLoading.value = false;
            }
        }

        // 加载报告
        async function loadReport() {
            reportLoading.value = true;
            try {
                const res = await fetch('/api/report');
                todayReport.value = await res.json();
            } catch (e) {
                console.error('Report load error:', e);
            } finally {
                reportLoading.value = false;
            }
        }

        // 语言统计（计算属性）
        const langStats = computed(() => {
            const projs = todayData.projects || [];
            const langCount = {};
            projs.forEach(p => {
                const lang = p.language || '其他';
                langCount[lang] = (langCount[lang] || 0) + 1;
            });
            const total = projs.length;
            return Object.entries(langCount)
                .map(([name, count]) => ({ name, count, pct: Math.round(count / total * 100) }))
                .sort((a, b) => b.count - a.count);
        });

        // 历史趋势
        const historyByDate = computed(() => {
            return historyData.value.map(d => ({
                date: d.date,
                count: d.projects.length,
            })).reverse();
        });

        // 自动加载
        onMounted(() => {
            loadToday();
        });

        // 展开/折叠仓库详情
        function toggleDetails(p) {
            p._showDetails = !p._showDetails;
        }

        // Tab切换时加载
        function onTabChange(newTab) {
            tab.value = newTab;
            if (newTab === 'history') loadHistory();
            if (newTab === 'report') {
                loadReport();
                loadHistory(); // 也加载历史用于图表
            }
        }

        // 监控tab变化
        watch(tab, (newVal) => {
            onTabChange(newVal);
        });

        return {
            tab, loading, error, todayData,
            historyLoading, historyData,
            reportLoading, todayReport,
            formatNum, renderMarkdown,
            hasDetails, getDetail, hasLangPct, getLangColor,
            loadToday, refreshData, submitRating,
            loadHistory, loadReport,
            langStats, historyByDate,
            toggleDetails, onTabChange,
        };
    }
});

app.mount('#app');
