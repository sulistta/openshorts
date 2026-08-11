# raplifebr — fluxo de licenciamento e operação

**Estado:** pesquisa e preparação; nenhum vídeo foi baixado, editado ou publicado.

## Objetivo

Construir uma fila de conteúdo para Reels, TikTok e Shorts sem presumir que um
vídeo encontrado no YouTube esteja liberado para republicação. O YouTube usa
Content ID para identificar e administrar conteúdo protegido por direitos
autorais.[1] O Instagram também orienta que a publicação respeite copyright.[2]

A meta de 10 vídeos por dia deve ser tratada como cadência de teste, não como
garantia de crescimento. Não serão usados bots de curtidas, comentários,
seguidores ou mensagens, nem reposts automáticos sem autorização.

## Direitos que precisam ser confirmados por faixa

Para cada combinação de música e vídeo, registrar separadamente:

- **Master/fonograma:** autorização do titular da gravação que será usada.
- **Composição/editorial:** autorização dos compositores e editores quando a
  licença exigida envolver a obra musical.
- **Sincronização/master use:** autorização para combinar a música com imagens,
  cortes, legendas, overlays e chamadas.
- **Imagem e vídeo:** autorização do videoclipe, performance, pessoas,
  cenários, marcas e material de terceiros.
- **Distribuição:** Instagram Reels, TikTok e YouTube Shorts, território,
  prazo, publicação orgânica, anúncios, monetização e quantidade de posts.
- **Content ID/claims:** quem fará a liberação de reivindicações e como tratar
  bloqueios, silenciamentos e takedowns.

O ECAD descreve seu papel brasileiro como arrecadação e distribuição de
direitos autorais de músicas; a UBC é uma entidade de autores e compositores.
Elas são pontos de referência para mapear a cadeia de titulares, mas não
substituem a licença específica do fonograma e do uso audiovisual.[5][6]

## Procedimento de licenciamento

1. **Identificar a versão exata:** título, artista, ISRC quando disponível,
   gravadora/distribuidora, compositores, editora e URL do canal oficial.
2. **Localizar o decisor:** artista, empresário, gravadora, editora ou
   distribuidora; começar sempre por um contato público/oficial.
3. **Enviar pedido escrito** descrevendo a página `raplifebr`, formatos,
   plataformas, território, prazo, volume de até 10 posts/dia, monetização,
   impulsionamento, edição e atribuição.
4. **Obter autorização assinada** ou contrato/termos inequívocos. Guardar a
   prova fora do chat, no ledger de direitos local, sem inserir senhas ou
   tokens.
5. **Validar limitações:** uso apenas em determinada plataforma, áudio
   encurtado, vídeo específico, proibição de anúncios, prazo de expiração ou
   obrigação de marcar o artista.
6. **Registrar aprovação por faixa e por plataforma** antes de enviar a URL ao
   pipeline.
7. **Revisar claims e remoções** semanalmente; suspender a faixa se houver
   disputa até o titular confirmar a liberação.

### Campos mínimos do ledger

`artist`, `track`, `isrc`, `master_owner`, `publisher`, `visual_owner`,
`licensee`, `territory`, `platforms`, `term_start`, `term_end`,
`organic_or_paid`, `monetization`, `max_posts`, `editing_allowed`,
`content_id_clearance`, `proof_location`, `approval_status`, `notes`.

## Rota segura enquanto a licença não existe

- Usar visuais próprios ou licenciados e publicar o vídeo sem fonograma
  incorporado; adicionar somente áudio nativo/licenciado dentro da plataforma,
  quando a conta e o território oferecerem essa opção.
- Usar faixas próprias ou Creative Commons cuja licença permita uso comercial,
  edição e distribuição nas plataformas escolhidas.
- Não baixar videoclipe ou faixa de terceiros do YouTube para recortar e
  republicar apenas porque a URL é pública.

## Limites técnicos atuais do OpenShorts

O MCP versionado no código-fonte expõe apenas `process_video`,
`get_job_status`, `list_clips` e `add_subtitles`. Ele é local/loopback e gera
arquivos; o README do projeto informa que o aplicativo não publica diretamente
nas redes sociais. Portanto, depois da autorização, o fluxo será:

1. enviar apenas uma fonte autorizada ao `process_video` com
   `confirm_rights=true`;
2. aguardar `get_job_status` até `completed`;
3. selecionar em `list_clips` e ajustar com `add_subtitles`;
4. revisar manualmente a licença, legenda e capa;
5. publicar por uma integração oficial da plataforma ou por um agendador que o
   usuário controle.

A API de publicação do Instagram e a Content Posting API do TikTok têm
requisitos próprios de conta, aplicativo e permissões; não vou contorná-los nem
armazenar credenciais no chat.[3][4]

## Pedido de licença — texto-base

> Olá, [titular]. A página `raplifebr` pretende publicar cortes verticais
> curtos para Reels, TikTok e YouTube Shorts usando [faixa/ISRC] e [material
> visual]. Solicitamos autorização escrita para sincronização, edição,
> reprodução e publicação orgânica em [território], de [data] a [data], em até
> [quantidade] posts, com/sem monetização e com/sem mídia paga. A autorização
> deve indicar o titular autorizado, plataformas, versão do fonograma, uso de
> legendas/overlays, atribuição, tratamento de Content ID e procedimento para
> remoção. Podemos submeter cada corte para aprovação antes da publicação.

Este texto é um ponto de partida operacional, não aconselhamento jurídico.

## Próximas decisões necessárias

- Confirmar as versões exatas de **CJota** e **WIU** que serão licenciadas; a
  busca inicial já registrou candidatos, mas não substitui a confirmação do
  fonograma e do titular.
- Definir se a página terá apenas conteúdo licenciado/autor próprio ou também
  visuais próprios com áudio nativo das plataformas.
- Escolher o caminho de publicação após as licenças: API oficial/agendador
  conectado pelo usuário ou preparação local para publicação manual.

## Sources

[1] https://support.google.com/youtube/answer/2797370?hl=pt-BR — Como funciona o Content ID - Ajuda do YouTube
[2] https://help.instagram.com/126382350847838 — Copyright on Instagram - Instagram Help Center
[3] https://developers.facebook.com/docs/instagram-api/guides/content-publishing — Instagram API - Content Publishing
[4] https://developers.tiktok.com/doc/content-posting-api-get-started — TikTok Content Posting API
[5] https://www.ecad.org.br — ECAD
[6] https://www.ubc.org.br — UBC
