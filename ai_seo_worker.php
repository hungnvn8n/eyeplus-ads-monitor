<?php
if (($_GET['k'] ?? '') !== '__TOKEN__') { http_response_code(404); exit; }
header('Content-Type: application/json; charset=utf-8');
@set_time_limit(280); @ini_set('memory_limit','256M');

$pats = [
  '/usr/local/apache/domlogs/jsrmamtihosting/kinhmateyeplus.com*',
  '/home/jsrmamtihosting/logs/kinhmateyeplus.com*',
  '/usr/local/apache/domlogs/kinhmateyeplus.com*',
  '/home/jsrmamtihosting/access-logs/kinhmateyeplus.com*',
];
$files = [];
$chon = $_GET['f'] ?? '';
foreach ($pats as $p) foreach ((array) glob($p) as $f)
  if (is_file($f) && is_readable($f) && filesize($f) > 0
      && ($chon==='' || strpos($f,$chon)!==false)) $files[$f] = filesize($f);

if (($_GET['mode'] ?? '') === 'list') { echo json_encode($files, 128|256); exit; }

// bot AI va nguon gioi thieu tu AI
$bots = ['OAI-SearchBot'=>'ChatGPT (tim kiem)','ChatGPT-User'=>'ChatGPT (nguoi dung bam)',
 'GPTBot'=>'OpenAI (hoc du lieu)','PerplexityBot'=>'Perplexity','Perplexity-User'=>'Perplexity (nguoi dung)',
 'Claude-SearchBot'=>'Claude (tim kiem)','ClaudeBot'=>'Anthropic (hoc du lieu)','Claude-User'=>'Claude (nguoi dung)',
 'Google-Extended'=>'Google Gemini','meta-externalagent'=>'Meta AI','Bytespider'=>'TikTok/ByteDance',
 'Amazonbot'=>'Amazon','Applebot-Extended'=>'Apple','bingbot'=>'Bing','Googlebot'=>'Google'];
$refs = ['chatgpt.com'=>'ChatGPT','chat.openai.com'=>'ChatGPT','perplexity.ai'=>'Perplexity',
 'gemini.google.com'=>'Gemini','claude.ai'=>'Claude','copilot.microsoft.com'=>'Copilot',
 'bing.com'=>'Bing','google.com'=>'Google'];

$botHit=[]; $botDay=[]; $botIp=[]; $refHit=[]; $refDay=[]; $refUrl=[]; $botUrl=[]; $lines=0; $doc=[];
$max = (int)($_GET['max'] ?? 4000000);

foreach ($files as $f => $sz) {
  $gz = substr($f,-3)==='.gz';
  $h = $gz ? @gzopen($f,'rb') : @fopen($f,'rb');
  if (!$h) continue;
  $n=0;
  while (!($gz ? gzeof($h) : feof($h))) {
    $l = $gz ? gzgets($h,4096) : fgets($h,4096);
    if ($l===false) break;
    $lines++; $n++;
    if ($lines > $max) break 2;
    // ngay: [18/Oct/2026:...
    $d = (preg_match('~\[(\d{2}/\w{3}/\d{4}):~',$l,$m)) ? $m[1] : '?';
    // url yeu cau
    $u = (preg_match('~"(?:GET|HEAD|POST) ([^ "]+)~',$l,$m2)) ? $m2[1] : '';
    foreach ($bots as $sig=>$ten) {
      if (stripos($l,$sig)!==false) {
        $botHit[$ten]=($botHit[$ten]??0)+1;
        $botDay[$ten][$d]=($botDay[$ten][$d]??0)+1;
        if($u){ $botUrl[$ten][$u]=($botUrl[$ten][$u]??0)+1; }
        if (preg_match('~^(\S+)~',$l,$mi)) { $botIp[$ten][$mi[1]]=($botIp[$ten][$mi[1]]??0)+1; }
        break;
      }
    }
    if (preg_match('~\s"https?://([^/"]+)~',$l,$m3)) {
      $host = strtolower($m3[1]);
      foreach ($refs as $sig=>$ten) {
        if (strpos($host,$sig)!==false) {
          $refHit[$ten]=($refHit[$ten]??0)+1;
          $refDay[$ten][$d]=($refDay[$ten][$d]??0)+1;
          if($u){ $refUrl[$ten][$u]=($refUrl[$ten][$u]??0)+1; }
          break;
        }
      }
    }
  }
  $doc[$f]=$n; $gz?gzclose($h):fclose($h);
}
foreach ($botUrl as $k=>&$v){ arsort($v); $v=array_slice($v,0,8,true); }
foreach ($refUrl as $k=>&$v){ arsort($v); $v=array_slice($v,0,8,true); }
arsort($botHit); arsort($refHit);
echo json_encode(['dong_da_doc'=>$lines,'file'=>$doc,'bot'=>$botHit,'bot_theo_ngay'=>$botDay,
  'bot_url'=>$botUrl,'bot_ip'=>$botIp,'nguon_ai'=>$refHit,'nguon_theo_ngay'=>$refDay,'nguon_url'=>$refUrl],
  JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES|JSON_PRETTY_PRINT);
